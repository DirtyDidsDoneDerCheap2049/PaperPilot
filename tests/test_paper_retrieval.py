import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.llm.local_embedding import LocalEmbedding
from src.knowledge.paper_retrieval import PaperRetriever, parent_spans
from src.llm.context_budget import prepare_reading
from src.analysis.question_evidence import extraction_messages


@pytest.fixture(scope='module')
def encoder():
    model=LocalEmbedding()
    if not model.available:pytest.fail('Prepare the release embedding model before running vector integration tests')
    return model


@pytest.fixture
def index(tmp_path,encoder):
    retriever=PaperRetriever(tmp_path,encoder)
    yield retriever
    retriever.close()


def add(index,pid,text):
    path=index.root/(pid+'.md');path.write_text(text,'utf-8')
    return index.index({'id':pid,'title':pid},path,text)


def test_real_cross_language_vectors_and_scope(index):
    add(index,'distill','# Method\n\nThe student learns by matching the frozen teacher network predictions. Knowledge distillation transfers learned representations through a feature imitation loss.\n\n# Results\n\nThe student improves with teacher supervision.')
    add(index,'weather','# Weather\n\nDaily rainfall and wind speeds are recorded by stations to forecast storms in coastal cities.')
    add(index,'routing','# Routing\n\nA shortest path algorithm routes packets between routers with congestion-aware network scheduling.')
    hits=index.search('教师网络指导学生，通过特征模仿损失迁移知识',top_k=3)
    assert hits[0]['paper_id']=='distill'
    assert 'vector' in hits[0]['methods']
    assert {h['paper_id'] for h in index.search('知识蒸馏',['weather'],3)}=={'weather'}
    assert not index.search('知识蒸馏',[],3)


def test_incremental_reuse_stale_source_and_prune(index,monkeypatch):
    text='# Experiments\n\nAn accuracy evaluation compares models on the held-out dataset.'
    assert add(index,'a',text)['status']=='indexed'
    original=index.encoder.encode
    def forbidden(*args):raise AssertionError('Unchanged source must not be re-encoded')
    monkeypatch.setattr(index.encoder,'encode',forbidden)
    assert add(index,'a',text)['status']=='reused'
    path=index.root/'a.md';path.write_text(text+' Changed.', 'utf-8')
    assert index.search('accuracy',['a'])==[]
    monkeypatch.setattr(index.encoder,'encode',original)
    assert index.index({'id':'a'},path,path.read_text('utf-8'))['status']=='indexed'
    assert index.search('accuracy',['a'])
    assert index.prune(set())==1
    assert index.status()['documents']==0
    assert not index.search('accuracy')


def test_retrieval_preserves_whole_transposed_table(index):
    table='| Method | Alpha | Beta |\n|---|---|---|\n| Dataset EPE | 0.34 | 0.43 |\n| Runtime | 120 ms | 60 ms |'
    text='# Evaluation\n\n'+table+'\n\nThe evaluation uses the standard held-out test split.'
    add(index,'table',text)
    hit=index.search('Dataset EPE', ['table'],1)[0]
    assert table in hit['text']
    assert text[hit['start']:hit['end']]==hit['text']
    assert hit['source_path']=='table.md'
    assert 'text' in hit['methods']


def test_model_context_reads_long_source_without_old_truncation(tmp_path):
    text='前言 '*17000+'\n\n# 核心结果\n\n中间的关键数值 0.34。\n\n'+'Conclusion '*8000
    owner=SimpleNamespace(llm=SimpleNamespace(_base_url='https://api.deepseek.com',max_output_tokens=65536),retriever=None)
    content,info=asyncio.run(prepare_reading(owner,SimpleNamespace(workspace_root=tmp_path),{'id':'a'},text,'abstract','实验结果'))
    assert len(text)>120000 and content==text and info['input_complete']
    message=json.loads(extraction_messages({'objective':'结果'}, {'id':'a'},content,True)[1]['content'])
    assert message['content']==text


def test_small_context_marks_partial_and_keeps_paragraphs(tmp_path):
    text='\n\n'.join(['# Introduction']+['Unrelated background. '*250]*20+['# Experiments','Evaluation score is 0.34 on ExampleDataset.'])
    owner=SimpleNamespace(llm=SimpleNamespace(context_window=32000,max_output_tokens=4096),retriever=None)
    content,info=asyncio.run(prepare_reading(owner,SimpleNamespace(workspace_root=tmp_path),{'id':'a'},text,'abstract','ExampleDataset evaluation score'))
    assert not info['input_complete'] and 'Evaluation score is 0.34 on ExampleDataset.' in content
    assert len(content.encode('utf-8'))<=info['input_byte_budget']


def test_index_does_not_activate_failed_generation(index,monkeypatch):
    add(index,'a','Original experiment result.')
    path=index.root/'a.md';path.write_text('A changed experiment result.', 'utf-8')
    def failed(texts):raise RuntimeError('injected interruption')
    monkeypatch.setattr(index.encoder,'encode',failed)
    with pytest.raises(RuntimeError):index.index({'id':'a'},path,path.read_text('utf-8'))
    assert not index.search('result',['a'])


def test_native_index_task_without_model_key(tmp_path,monkeypatch):
    import time
    import os
    from src.runtime.tasks import TaskService
    from fastapi.testclient import TestClient
    from src.app.factory import build_app
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    if executable:=os.getenv('READER_TEST_DESKTOP_EXE'):
        monkeypatch.setattr(TaskService,'worker_command',lambda self:[executable,'--worker',str(self.workspace)])
    app,_=build_app(tmp_path)
    path=tmp_path/'source.md';path.write_text('# Method\n\nKnowledge distillation transfers teacher features to the student.', 'utf-8')
    app.state.db.execute('INSERT INTO papers(id,title,parsed_markdown_path) VALUES(?,?,?)',('a','Teacher student transfer',str(path)))
    with TestClient(app) as client:
        reply=client.post('/api/retrieval/index',headers={'Idempotency-Key':'index-test','X-Reader-Client':'desktop'})
        assert reply.status_code==202,reply.text
        job_id=reply.json()['job_id']
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            job=client.get('/api/jobs/'+job_id).json()
            if job['status'] not in {'queued','running','cancel_requested'}:break
            time.sleep(.2)
        assert job['status']=='succeeded',job
        assert job['index_result']['indexed']==1 and job['index_result']['external_requests']==0
        hits=client.get('/api/retrieval/search',params={'q':'教师学生知识迁移'}).json()['hits']
        assert hits[0]['paper_id']=='a' and 'vector' in hits[0]['methods']
        reply=client.post('/api/retrieval/index',headers={'Idempotency-Key':'index-test','X-Reader-Client':'desktop'})
        assert reply.json()['job_id']==job_id


def test_full_read_defers_cold_index_instead_of_delaying_model(index,monkeypatch):
    path=index.root/'new.md';text='Complete local paper text.';path.write_text(text,'utf-8')
    def forbidden(*args):raise AssertionError('Full read must not wait for a cold embedding model')
    monkeypatch.setattr(index.encoder,'encode',forbidden)
    owner=SimpleNamespace(llm=SimpleNamespace(_base_url='https://api.deepseek.com'),retriever=index)
    content,info=asyncio.run(prepare_reading(owner,SimpleNamespace(workspace_root=index.root),{'id':'new'},text,str(path),'method'))
    assert content==text and info['input_complete']
    assert (index.directory/'needs-update').is_file()


def test_optional_index_failure_does_not_block_full_read(index,monkeypatch):
    path=index.root/'new.md';text='Complete local paper text.';path.write_text(text,'utf-8')
    def failed(*args):raise OSError('injected index failure')
    monkeypatch.setattr(index,'is_current',failed)
    owner=SimpleNamespace(llm=SimpleNamespace(_base_url='https://api.deepseek.com'),retriever=index)
    content,info=asyncio.run(prepare_reading(owner,SimpleNamespace(workspace_root=index.root),{'id':'new'},text,str(path),'method'))
    assert content==text and info['input_complete'] and info['index_error']=='OSError'


def test_background_index_yields_to_interactive_task(tmp_path):
    import sys
    from src.runtime.tasks import TaskService,native_binary
    from src.knowledge.sqlite_store import SQLiteStore
    script=tmp_path/'slow_index.py';script.write_text('import time\ntime.sleep(60)\n','utf-8')
    db=SQLiteStore(tmp_path/'research.db');db.init_schema()
    class Broadcaster:
        async def broadcast(self,*args):pass
    service=TaskService(tmp_path,db,Broadcaster())
    service.worker_command=lambda:[sys.executable,str(script)]
    async def run():
        execution=None
        try:
            service.process=await asyncio.create_subprocess_exec(str(native_binary()),str(tmp_path/'tasks.db'),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            await service.rpc('submit',id='index',session_id='__paper_index__',request_key='index',payload={'mode':'library_index','background':True})
            job=await service.rpc('claim')
            execution=asyncio.create_task(service._execute(job))
            async def started():
                while service.worker is None:await asyncio.sleep(.01)
            await asyncio.wait_for(started(),5)
            await service.rpc('submit',id='research',session_id='s',request_key='research',payload={'mode':'research','message':'Read my papers'})
            await asyncio.wait_for(execution,5)
            state=await service.rpc('get',id='index')
            assert state['status']=='cancelled' and '优先处理新任务' in state['error']
            assert (tmp_path/'db/paper-retrieval/needs-update').is_file()
            assert (await service.rpc('get',id='research'))['status']=='queued'
        finally:
            if execution and not execution.done():
                service.stopping=True
                await asyncio.wait_for(execution,5)
            await service.close()
    try:asyncio.run(run())
    finally:db.close()


def test_agent_retrieve_executes_scoped_local_vector_search(tmp_path,monkeypatch,encoder):
    from src.app.factory import build_app
    from src.agents.base import AgentContext,AgentResult
    from src.analysis.adaptive_research import execute_research
    from src.analysis.research_plan import normalize_plan
    from src.analysis.provenance import bind_profile
    app,config=build_app(tmp_path);owner=app.state.orchestrator
    path=tmp_path/'a.md';text='# Method\n\nTeacher knowledge is transferred to a student using feature imitation.';path.write_text(text,'utf-8')
    paper={'id':'a','title':'Teacher student transfer','parsed_markdown_path':str(path)}
    app.state.retriever.encoder=encoder
    app.state.retriever.index(paper,path,text)
    class Model:
        calls=0;max_calls=200
        async def chat_json(self,messages,**kwargs):
            self.calls+=1
            state=json.loads(messages[-1]['content'])
            if self.calls==1:
                assert 'retrieve' in state['allowed_actions']
                return {'action':'retrieve','queries':['教师学生知识迁移'],'paper_ids':['a']}
            if self.calls==2:
                assert state['local_evidence'][0]['hits'][0]['paper_id']=='a'
                return {'action':'read','paper_ids':['a']}
            return {'action':'analyze'}
    owner.llm=Model();config['research']['max_agent_steps']=5
    async def parse(agent,ctx,value):return AgentResult(status='completed',data={'parsed':['a']})
    async def extract(*args,**kwargs):return [bind_profile({'paper_id':'a','has_fulltext':True,'evidence':[{'quote':'Teacher knowledge is transferred to a student using feature imitation.'}]},text,'a.md')]
    async def analyze(*args):return {'answer_status':'answered_in_retrieved_sources','missing_information':[]}
    monkeypatch.setattr('src.agents.parse_agent.ParseAgent.run',parse)
    monkeypatch.setattr(owner,'_extract_innovations',extract)
    monkeypatch.setattr('src.analysis.question_evidence.analyze_question',analyze)
    async def run():
        await owner.kb.upsert_paper(paper)
        plan=normalize_plan({'task_type':'question_answer','source_scope':'provided','adaptive':True},'核对上传论文的知识迁移方法')
        return await execute_research(owner,AgentContext(workspace_root=tmp_path,session_id='test',config=config),{'research_plan':plan},['a'])
    try:
        papers,profiles,analysis,_=asyncio.run(run())
        assert len(papers)==1 and profiles[0]['paper_id']=='a'
        hit=analysis['agent_decisions'][0]
        assert hit['action']=='retrieve' and hit['paper_ids']==['a']
        assert analysis['reading_coverage']['status']=='complete'
    finally:
        app.state.retriever.close();app.state.vs.close();app.state.db.close()
