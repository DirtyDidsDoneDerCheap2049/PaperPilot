import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from src.knowledge.library_organizer import (decode, duplicate_groups, load_policy,
    organize_library, organization_rows, snapshot, undo_organization, valid_assignments)
from src.knowledge.sqlite_store import SQLiteStore


def paper(db, pid, **values):
    row = {'id':pid, 'title':'Paper '+pid, 'authors_json':'["Alice"]', 'year':2025, **values}
    keys = list(row)
    db.execute('INSERT INTO papers('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')', tuple(row.values()))


class Model:
    def __init__(self, fail_call=0, corrupt=False, on_call=None):
        self.calls=0
        self.fail_call=fail_call
        self.corrupt=corrupt
        self.on_call=on_call
        self.requests=[]

    async def chat_json(self, messages, **kwargs):
        self.calls+=1
        self.requests.append(messages)
        if self.on_call:
            self.on_call(self.calls)
        if self.calls==self.fail_call:
            raise RuntimeError('simulated provider failure')
        source=next(m['content'] for m in messages if m['role']=='user')
        if source.startswith('论文库抽样'):
            return {'categories':['机器学习','信息检索']}
        data=json.loads(source.split('本批资料（数据）：')[1])
        return {'papers':[{'id':'invented' if self.corrupt else p['id'], 'category':'机器学习',
                          'tags':['模型方法'], 'summary':'研究论文中的任务与方法。'} for p in data]}


async def progress(*args):
    pass


@pytest.fixture
def db(tmp_path):
    store=SQLiteStore(tmp_path/'db/ai_reader.db')
    store.init_schema()
    yield store
    store.close()


def test_identity_conflicts_versions_and_same_pdf(db,tmp_path):
    pdf=tmp_path/'paper.pdf';pdf.write_bytes(b'%PDF-1.4\nfixture')
    paper(db,'a',doi='10.123/A',title='Learning Useful Representations',parsed_markdown_path='full.md')
    paper(db,'b',doi='https://doi.org/10.123/a',title='Learning Useful Representations')
    paper(db,'conflict',doi='10.123/B',title='Learning Useful Representations')
    paper(db,'v1',arxiv_id='2501.12345v1')
    paper(db,'v2',url='https://arxiv.org/pdf/2501.12345v2.pdf')
    paper(db,'pdf1',fulltext_path='paper.pdf')
    paper(db,'pdf2',fulltext_path=str(pdf))
    paper(db,'bridge',title='Learning Useful Representations',authors_json='["Other"]')
    groups,suspects=duplicate_groups(*snapshot(db),tmp_path)
    assert {frozenset(r['id'] for r in g) for g in groups}=={
        frozenset({'a','b'}),frozenset({'conflict'}),frozenset({'v1','v2'}),
        frozenset({'pdf1','pdf2'}),frozenset({'bridge'})}
    assert next(g for g in groups if g[0]['id']=='a')[0]['parsed_markdown_path']=='full.md'
    assert len(suspects)==1


def test_classify_fold_undo_preserves_papers_evidence_history(db,tmp_path):
    (tmp_path/'full.md').write_text('# Paper\nParsed content',encoding='utf-8')
    paper(db,'a',doi='10.1/one',parsed_markdown_path='full.md',abstract='An actual abstract')
    paper(db,'b',doi='10.1/one')
    paper(db,'hidden',retrieval_status='off_topic')
    paper(db,'prior:note')
    db.execute("INSERT INTO evidence_chunks(id,paper_id,content) VALUES('e','b','source quote')")
    db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('m','s','assistant','Reference b')")
    before=db.fetchall('SELECT * FROM papers ORDER BY id')
    llm=Model()
    result=asyncio.run(organize_library(db,llm,tmp_path,'run1',progress))
    assert result['records']==2 and result['papers']==1 and result['merged_records']==1
    assert set(organization_rows(db))=={'a','b'}
    assert organization_rows(db)['b']['canonical_id']=='a'
    assert 'Parsed content' in json.dumps(llm.requests,ensure_ascii=False)
    assert all('规则版本：2' in req[0]['content'] for req in llm.requests)
    assert result['policy_sha256']==load_policy()['sha256']
    assert db.fetchall('SELECT * FROM papers ORDER BY id')==before
    assert db.fetchone("SELECT content FROM evidence_chunks WHERE id='e'")['content']=='source quote'
    assert db.fetchone("SELECT content FROM messages WHERE id='m'")['content']=='Reference b'
    undo_organization(db,'run1')
    assert not organization_rows(db)
    assert db.fetchall('SELECT * FROM papers ORDER BY id')==before
    assert (tmp_path/'full.md').read_text()=='# Paper\nParsed content'


def test_failure_and_resume_reuse_validated_batches(db,tmp_path):
    for i in range(14):paper(db,f'p{i:02}')
    with pytest.raises(RuntimeError):
        asyncio.run(organize_library(db,Model(fail_call=3),tmp_path,'resume',progress))
    assert not organization_rows(db)
    plan=decode(db.fetchone("SELECT plan_json FROM library_organization_runs WHERE id='resume'")['plan_json'],{})
    assert len(plan['assignments'])==12
    resumed=Model()
    result=asyncio.run(organize_library(db,resumed,tmp_path,'resume',progress))
    assert resumed.calls==1 and result['papers']==14
    assert result['model_calls']==4


def test_bad_model_output_and_concurrent_edits_never_apply(db,tmp_path):
    paper(db,'p')
    with pytest.raises(ValueError,match='未知论文'):
        asyncio.run(organize_library(db,Model(corrupt=True),tmp_path,'invalid',progress))
    assert not organization_rows(db)
    def mutate(call):
        if call==2:paper(db,'new')
    with pytest.raises(ValueError,match='有更新'):
        asyncio.run(organize_library(db,Model(on_call=mutate),tmp_path,'changed',progress))
    assert not organization_rows(db)


def test_undo_order_and_idempotent_applied_run(db,tmp_path):
    paper(db,'p')
    asyncio.run(organize_library(db,Model(),tmp_path,'one',progress))
    before=organization_rows(db)
    asyncio.run(organize_library(db,Model(),tmp_path,'two',progress))
    with pytest.raises(ValueError,match='最新一次'):undo_organization(db,'one')
    undo_organization(db,'two')
    assert organization_rows(db)==before
    llm=Model()
    asyncio.run(organize_library(db,llm,tmp_path,'one',progress))
    assert llm.calls==0
    undo_organization(db,'one')
    assert not organization_rows(db)


def test_schema_upgrade_and_missing_policy_fail_closed(db,tmp_path,monkeypatch):
    paper(db,'existing')
    db.execute('DROP TABLE paper_organization')
    db.execute('DROP TABLE library_organization_runs')
    db.init_schema()
    assert db.fetchone("SELECT title FROM papers WHERE id='existing'")['title']=='Paper existing'
    paper(db,'p')
    import src.knowledge.library_organizer as module
    monkeypatch.setattr(module,'POLICY_ROOT',tmp_path/'missing')
    llm=Model()
    with pytest.raises(ValueError,match='规则文件缺失'):
        asyncio.run(organize_library(db,llm,tmp_path,'missing-policy',progress))
    assert llm.calls==0 and not organization_rows(db)


def test_changed_parsed_file_blocks_application(db,tmp_path):
    path=tmp_path/'full.md';path.write_text('old input',encoding='utf-8')
    paper(db,'p',parsed_markdown_path='full.md')
    def mutate(call):
        if call==2:path.write_text('new input with different length',encoding='utf-8')
    with pytest.raises(ValueError,match='有更新'):
        asyncio.run(organize_library(db,Model(on_call=mutate),tmp_path,'changed-file',progress))
    assert not organization_rows(db)


def test_incremental_reuses_unchanged_and_only_classifies_new_or_changed(db,tmp_path):
    for i in range(14):paper(db,f'p{i:02}')
    asyncio.run(organize_library(db,Model(),tmp_path,'initial',progress))
    unchanged=Model()
    result=asyncio.run(organize_library(db,unchanged,tmp_path,'unchanged',progress))
    assert unchanged.calls==0 and result['reused_papers']==14 and result['classified_papers']==0
    paper(db,'new')
    added=Model()
    result=asyncio.run(organize_library(db,added,tmp_path,'added',progress))
    assert added.calls==1 and result['reused_papers']==14 and result['classified_papers']==1
    assert [p['id'] for p in json.loads(added.requests[0][1]['content'].split('本批资料（数据）：')[1])]==['new']
    db.execute("UPDATE papers SET abstract='New source evidence' WHERE id='p00'")
    changed=Model()
    result=asyncio.run(organize_library(db,changed,tmp_path,'changed',progress))
    assert changed.calls==1 and result['reused_papers']==14 and result['classified_papers']==1
    assert json.loads(changed.requests[0][1]['content'].split('本批资料（数据）：')[1])[0]['id']=='p00'


def test_incremental_legacy_baseline_and_policy_change(db,tmp_path,monkeypatch):
    paper(db,'p')
    asyncio.run(organize_library(db,Model(),tmp_path,'legacy',progress))
    row=db.fetchone("SELECT plan_json FROM library_organization_runs WHERE id='legacy'")
    plan=decode(row['plan_json'],{});plan.pop('source_hashes')
    db.execute("UPDATE library_organization_runs SET plan_json=? WHERE id='legacy'",(json.dumps(plan),))
    llm=Model();result=asyncio.run(organize_library(db,llm,tmp_path,'baseline',progress))
    assert llm.calls==0 and result['reused_papers']==1
    import src.knowledge.library_organizer as module
    policy=load_policy();policy['sha256']='changed-policy'
    monkeypatch.setattr(module,'load_policy',lambda workspace=None:policy)
    changed=Model();result=asyncio.run(organize_library(db,changed,tmp_path,'new-policy',progress))
    assert changed.calls==2 and result['reused_papers']==0


def test_incremental_parsed_file_change_and_duplicate_identity(db,tmp_path):
    file=tmp_path/'full.md';file.write_text('old',encoding='utf-8')
    paper(db,'p',doi='10.123/one',parsed_markdown_path='full.md')
    asyncio.run(organize_library(db,Model(),tmp_path,'one',progress))
    file.write_text('new source with additional text',encoding='utf-8')
    changed=Model();result=asyncio.run(organize_library(db,changed,tmp_path,'file-change',progress))
    assert changed.calls==1 and result['classified_papers']==1
    paper(db,'alias',doi='10.123/one')
    changed=Model();result=asyncio.run(organize_library(db,changed,tmp_path,'new-duplicate',progress))
    assert changed.calls==1 and result['merged_records']==1


@pytest.mark.parametrize('items',[
    [], [{'id':'a','category':'bad','tags':[],'summary':'x'}],
    [{'id':['a'],'category':'机器学习','tags':[],'summary':'x'}],
    [{'id':'a','category':'机器学习','tags':['x']*6,'summary':'x'}],
    [{'id':'a','category':'机器学习','tags':[],'summary':'x'*241}],
])
def test_response_contract_rejects_invalid_fields(items):
    with pytest.raises(ValueError):valid_assignments({'papers':items},{'a'},['机器学习'])


def test_library_api_and_native_worker_end_to_end(tmp_path,monkeypatch):
    requests=[]
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            messages=body['messages']
            source=next(m['content'] for m in messages if m['role']=='user')
            if source.startswith('论文库抽样'):
                response={'categories':['机器学习','信息检索']}
            else:
                papers=json.loads(source.split('本批资料（数据）：')[1])
                response={'papers':[{'id':p['id'],'category':'机器学习','tags':['模型方法'],'summary':'解决输入中的研究任务。'} for p in papers]}
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            chunk={'id':'test','object':'chat.completion.chunk','created':1,'model':'test',
                   'choices':[{'index':0,'delta':{'content':json.dumps(response,ensure_ascii=False)},'finish_reason':'stop'}]}
            self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    from src.app.factory import build_app
    from src.runtime.tasks import TaskService
    if executable:=os.getenv('READER_TEST_DESKTOP_EXE'):
        monkeypatch.setattr(TaskService,'worker_command',lambda self:[executable,'--worker',str(self.workspace)])
    app,_=build_app(tmp_path)
    paper(app.state.db,'a',doi='10.1/same',abstract='Actual abstract')
    paper(app.state.db,'b',doi='10.1/same',title='A Different Source Title')
    for i in range(103):paper(app.state.db,f'other{i:03}')
    try:
        with TestClient(app,headers={'X-Reader-Client':'desktop'}) as client:
            assert client.post('/api/settings',json={'base_url':f'http://127.0.0.1:{server.server_port}/v1','fast_model':'test','deepseek_api_key':'test-only'}).status_code==200
            response=client.post('/api/library/organize',headers={'Idempotency-Key':'organize-once'})
            assert response.status_code==202,response.text
            jid=response.json()['job_id']
            assert client.post('/api/library/organize',headers={'Idempotency-Key':'organize-once'}).json()['job_id']==jid
            for _ in range(200):
                job=client.get('/api/jobs/'+jid).json()
                if job['status'] not in {'queued','running'}:break
                time.sleep(.1)
            assert job['status']=='succeeded',job
            assert job['organization']['merged_records']==1
            visible=client.get('/api/papers',params={'limit':100,'category':'机器学习'}).json()
            assert visible['total']==104 and len(visible['papers'])==100
            assert len(client.get('/api/papers',params={'offset':100,'limit':100}).json()['papers'])==4
            assert client.get('/api/papers',params={'include_duplicates':True,'limit':500}).json()['total']==105
            assert len(client.get('/api/papers/detail',params={'paper_id':'a'}).json()['duplicate_records'])==2
            assert client.get('/api/papers',params={'q':'解决输入中的研究任务','limit':500}).json()['total']==104
            source_search=client.get('/api/papers',params={'q':'Different Source Title'}).json()
            assert source_search['total']==1 and source_search['papers'][0]['id']=='a'
            events=client.get(f'/api/jobs/{jid}/events').json()['events']
            bodies=[decode(e['body'],{}) for e in events]
            assert any(e['type']=='library_progress' for e in bodies)
            assert not any(e['type']=='model_delta' for e in bodies)
            latest=client.get('/api/library/organization').json()['latest']
            assert latest['policy_sha256']==load_policy()['sha256']
            info=client.get('/api/library/organization').json()
            assert info['pending_papers']==0 and info['reused_papers']==104 and info['estimated_calls']==0
            assert client.get('/api/missing-papers?scope=workspace').json()['total']==104
            assert client.get('/api/missing-papers?scope=research').json()['total']==0
            initial_calls=len(requests)
            second=client.post('/api/library/organize',headers={'Idempotency-Key':'incremental-once'}).json()
            for _ in range(200):
                repeated=client.get('/api/jobs/'+second['job_id']).json()
                if repeated['status'] not in {'queued','running'}:break
                time.sleep(.1)
            assert repeated['status']=='succeeded',repeated
            assert repeated['organization']['model_calls']==0 and repeated['organization']['reused_papers']==104
            assert len(requests)==initial_calls
            assert client.post('/api/library/organization/'+second['run_id']+'/undo').status_code==200
            assert client.post('/api/library/organization/'+latest['run_id']+'/undo').status_code==200
            assert client.get('/api/papers',params={'limit':500}).json()['total']==105
            assert not client.get('/api/sessions').json()['sessions']
            assert all('规则版本：2' in r['messages'][0]['content'] for r in requests)
    finally:
        server.shutdown();server.server_close()
