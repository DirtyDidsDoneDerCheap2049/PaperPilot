import json
import os
import subprocess
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]

class Native:
    def __init__(self,path):
        binary=ROOT/'build'/('reader_tasks.exe' if os.name=='nt' else 'reader_tasks')
        self.p=subprocess.Popen([str(binary),str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    def call(self,op,**kwargs):
        self.p.stdin.write(json.dumps(dict(op=op,**kwargs))+'\n'); self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())
    def close(self):
        self.p.stdin.close(); self.p.wait(timeout=5)
    def submit(self,id='j',session='s',key='k',payload=None):
        return self.call('submit',id=id,session_id=session,request_key=key,payload=payload or {'message':'中文问题'})

def test_native_idempotency_cancel_and_token(tmp_path):
    n=Native(tmp_path/'任务.db')
    try:
        assert n.call('ping')['ok']
        assert n.submit()['data']['status']=='queued'
        assert n.submit(id='other')['data']['id']=='j'
        assert n.submit(payload={'message':'different'})['error']=='idempotency_conflict'
        assert n.submit(id='j2',key='k2')['error']=='conflict'
        token=n.call('claim')['data']['token']
        assert n.call('finish',id='j',token=token-1,status='succeeded')['error']=='stale_attempt'
        assert n.call('cancel',id='j')['data']['status']=='cancel_requested'
        assert n.call('finish',id='j',token=token,status='succeeded')['data']['status']=='cancelled'
        assert not n.call('event',id='j',token=token,body={})['ok']
    finally: n.close()

def test_native_crash_recovery_and_retry(tmp_path):
    path=tmp_path/'tasks.db'; n=Native(path)
    n.submit(); first=n.call('claim')['data']['token']
    n.p.kill(); n.p.wait()
    n=Native(path)
    try:
        assert n.call('get',id='j')['data']['status']=='interrupted'
        assert n.call('retry',id='j')['data']['status']=='queued'
        second=n.call('claim')['data']['token']; assert second>first
        assert not n.call('finish',id='j',token=first,status='succeeded')['ok']
        assert n.call('finish',id='j',token=second,status='succeeded')['ok']
        assert n.call('finish',id='j',token=second,status='succeeded')['ok']
    finally: n.close()

def test_transaction_rollback(tmp_path):
    from src.knowledge.sqlite_store import SQLiteStore
    db=SQLiteStore(tmp_path/'test.db'); db.init_schema()
    db.execute("INSERT INTO sessions(id,workspace_id) VALUES('s','w')")
    with pytest.raises(Exception):
        with db.transaction():
            db.execute("DELETE FROM sessions WHERE id='s'")
            db.execute('BAD SQL')
    assert db.fetchone("SELECT id FROM sessions WHERE id='s'")
    db.close()

def test_secrets_round_trip(tmp_path):
    from src.runtime.settings import write_secrets,read_secrets
    value={'deepseek_api_key':'test-only-secret-never-real'}
    write_secrets(tmp_path,value)
    assert read_secrets(tmp_path)==value
    if os.name=='nt': assert value['deepseek_api_key'] not in (tmp_path/'.secrets.json').read_text()

def test_local_index(tmp_path):
    from src.knowledge.vector_store import LocalSearchStore,VectorDoc
    index=LocalSearchStore(tmp_path/'search.db')
    index.add_documents('papers',[VectorDoc(id='p',text='stereo matching with teacher supervision')])
    assert index.query('papers','stereo')[0].id=='p'
    index.close()

def test_local_api_settings_and_csrf(tmp_path,monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    from fastapi.testclient import TestClient
    from src.app.factory import build_app
    app,_=build_app(tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/health').status_code==200
        assert client.get('/api/settings/status').status_code==200
        assert client.post('/api/settings',json={'fast_model':'x'}).status_code==403
        headers={'X-Reader-Client':'desktop'}
        assert client.post('/api/settings',headers=dict(headers,Origin='https://evil.example'),json={}).status_code==403
        assert client.post('/api/settings',headers=headers,json={'base_url':'https://example.com/v1','deepseek_api_key':'test-only-secret','fast_model':'test-model'}).status_code==200
        body=client.get('/api/settings').json()
        assert body['llm']['has_key'] and body['llm']['base_url']=='https://example.com/v1'
        assert 'test-only-secret' not in json.dumps(body)
        assert not (tmp_path/'.env').exists()


def test_worker_through_native_and_local_provider(tmp_path,monkeypatch):
    if executable := os.getenv('READER_TEST_DESKTOP_EXE'):
        from src.runtime.tasks import TaskService
        monkeypatch.setattr(TaskService,'worker_command',lambda self:[executable,'--worker',str(self.workspace)])
    import threading,time
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
            chunk={'id':'test','object':'chat.completion.chunk','created':1,'model':'test','choices':[{'index':0,'delta':{'content':'离线测试回答：请核查原文。'},'finish_reason':'stop'}],
                   'usage':{'prompt_tokens':100,'completion_tokens':20,'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':20}}
            self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    from src.app.factory import build_app
    from fastapi.testclient import TestClient
    app,_=build_app(tmp_path)
    try:
        with TestClient(app,headers={'X-Reader-Client':'desktop'}) as client:
            assert client.post('/api/settings',json={'base_url':f'http://127.0.0.1:{server.server_port}/v1','fast_model':'test','deepseek_api_key':'test-only'}).status_code==200
            payload={'mode':'report_chat','session_id':'test-session','message':'请解释证据'}
            response=client.post('/api/chat',json=payload,headers={'Idempotency-Key':'same-request'})
            assert response.status_code==202,response.text
            id=response.json()['job_id']
            same=client.post('/api/chat',json=payload,headers={'Idempotency-Key':'same-request'})
            assert same.json()['job_id']==id
            for _ in range(100):
                job=client.get('/api/jobs/'+id).json()
                if job['status'] in {'succeeded','failed'}: break
                time.sleep(.2)
            assert job['status']=='succeeded',job
            assert job['usage_summary']['prompt_tokens']==100
            assert job['usage_summary']['completion_tokens']==20
            assert job['usage_summary']['input_cache_hit_rate']==.8
            messages=client.get('/api/sessions/test-session/messages').json()['messages']
            assert len(messages)==2
            assert '离线测试回答' in messages[-1]['content']
            assert client.get(f'/api/jobs/{id}/events').json()['events']
    finally:
        server.shutdown();server.server_close()


def test_sixty_paper_research_through_native_and_local_provider(tmp_path,monkeypatch):
    import threading,time,asyncio
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from src.runtime.tasks import TaskService
    from src.app.factory import build_app
    from fastapi.testclient import TestClient
    if executable:=os.getenv('READER_TEST_DESKTOP_EXE'):
        monkeypatch.setattr(TaskService,'worker_command',lambda self:[executable,'--worker',str(self.workspace)])
    received=[]
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            system=body['messages'][0]['content'];received.append(system)
            if '拆解研究想法' in system:
                answer={'research_question':'ExampleDataset最低EPE比较','target_task':'ExampleDataset benchmark',
                        'research_plan':{'task_type':'benchmark_comparison','adaptive':True,
                                         'comparison':{'groups':['精度'],'metric':'EPE','dataset':'ExampleDataset'}}}
            elif '下一步行动' in system:answer={'action':'deliver'}
            elif '按问题读取论文' in system:
                item=json.loads(body['messages'][-1]['content']);pid=item['metadata']['id']
                index=int(pid.rsplit(':',1)[1]);name='Model'+str(index)
                quote=f'{name} | ExampleDataset | EPE | 0.40'
                answer={'method_summary':'离线样例','evidence':[{'quote':quote}],
                        'benchmark_results':[{'model':name,'dataset':'ExampleDataset','metric':'EPE','value':.4,
                                              'group':'精度','row_role':'proposed','protocol':'same split','evidence_indices':[1]}]}
            elif '核对每个原表行' in system:
                rows=json.loads(body['messages'][-1]['content'])['rows']
                answer={'row_reviews':[{'row_id':r['row_id'],'scope_match':True,'group_verified':True,
                                        'group':'精度','comparison_key':'same split'} for r in rows]}
            elif '检查报告' in system:answer={'pass':False,'issues':['模拟初稿未直接回答问题']}
            else:answer='初稿未通过检查；仅用于离线验证部分报告交付。'
            text=answer if isinstance(answer,str) else json.dumps(answer,ensure_ascii=False)
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            chunk={'id':'fixture','object':'chat.completion.chunk','created':1,'model':'test',
                   'choices':[{'index':0,'delta':{'content':text},'finish_reason':'stop'}],
                   'usage':{'prompt_tokens':100,'completion_tokens':20,'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':20}}
            self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only');monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    app,_=build_app(tmp_path)
    async def seed():
        for index in range(60):
            path=tmp_path/'papers/parsed'/str(index)/'full.md';path.parent.mkdir(parents=True,exist_ok=True)
            quote=f'Model{index} | ExampleDataset | EPE | 0.40'
            text='# Offline synthetic method\n\n'+('This is an offline fixture for pipeline verification, not a published scientific result.\n'*20)+quote+'\n'
            path.write_text(text,encoding='utf-8')
            await app.state.kb.upsert_paper({'id':f'fixture:{index}','title':f'Model{index}: Offline synthetic method',
                                          'year':2026,'publication_date':'2026-02-01','retrieval_status':'parsed',
                                          'parsed_markdown_path':str(path)})
    try:
        asyncio.run(seed())
        with TestClient(app,headers={'X-Reader-Client':'desktop'}) as client:
            assert client.post('/api/settings',json={'base_url':f'http://127.0.0.1:{server.server_port}/v1',
                'fast_model':'test','reasoning_model':'test','deepseek_api_key':'test-only','search_deep_parse_top_k':60}).status_code==200
            response=client.post('/api/chat',json={'mode':'analysis','session_id':'sixty',
                'message':'所有ExampleDataset模型最低EPE是多少？','selected_paper_ids':[f'fixture:{i}' for i in range(60)]})
            assert response.status_code==202,response.text
            identifier=response.json()['job_id']
            for _ in range(500):
                job=client.get('/api/jobs/'+identifier).json()
                if job['status'] in {'succeeded','failed'}:break
                time.sleep(.1)
            assert job['status']=='succeeded',job
            row=app.state.db.fetchone("SELECT metadata_json FROM messages WHERE session_id='sixty' AND role='assistant'")
            meta=json.loads(row['metadata_json'])
            assert meta['innovations_extracted']==60 and meta['unprocessed_count']==0
            assert meta['reading_coverage']['completed']==60 and meta['reading_coverage']['read_limit']==60
            assert meta['partial_report'] and meta['answer_status']=='partial'
            report=(tmp_path/meta['report_path']).read_text(encoding='utf-8')
            assert '0.4 EPE' in report and '本轮研究结果（部分）' in report
            assert sum('按问题读取论文' in system for system in received)==60
            first_requests=len(received)
            assert job['usage_summary']['model_requests']==first_requests
            assert job['usage_summary']['prompt_tokens']==100*first_requests
            assert job['usage_summary']['completion_tokens']==20*first_requests
            assert job['usage_summary']['input_cache_hit_rate']==.8
            assert job['usage_summary']['local_evidence_cache_hits']==0
            response=client.post('/api/chat',json={'mode':'analysis','session_id':'sixty-repeat',
                'message':'所有ExampleDataset模型最低EPE是多少？','selected_paper_ids':[f'fixture:{i}' for i in range(60)]})
            assert response.status_code==202,response.text
            identifier=response.json()['job_id']
            for _ in range(500):
                repeated=client.get('/api/jobs/'+identifier).json()
                if repeated['status'] in {'succeeded','failed'}:break
                time.sleep(.1)
            assert repeated['status']=='succeeded',repeated
            assert sum('按问题读取论文' in system for system in received)==60
            assert repeated['usage_summary']['local_evidence_cache_hits']==60
            assert repeated['usage_summary']['model_requests']==len(received)-first_requests
            assert repeated['usage_summary']['model_requests']<first_requests
    finally:
        server.shutdown();server.server_close()


def test_discussion_to_research_keeps_context_and_uploaded_papers(tmp_path,monkeypatch):
    """An empty selection basket must not lose papers attached during discussion."""
    import asyncio,threading,time
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from src.runtime.tasks import TaskService
    from src.app.factory import build_app
    from fastapi.testclient import TestClient
    if executable:=os.getenv('READER_TEST_DESKTOP_EXE'):
        monkeypatch.setattr(TaskService,'worker_command',lambda self:[executable,'--worker',str(self.workspace)])
    received=[]
    question='核对这两篇论文在 ExampleDataset 的 EPE 是否低于 0.36'
    short='包含啊，你自己看一下'
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(body)
            system=body['messages'][0]['content']
            if '拆解研究想法' in system:
                answer={'context_relation':'continue','context_paper_ids':['fixture:a','fixture:b'],
                        'research_question':question,'target_task':'ExampleDataset',
                        'research_plan':{'task_type':'benchmark_comparison','source_scope':'provided','objective':question,'adaptive':True,
                                         'comparison':{'groups':['精度'],'metric':'EPE','dataset':'ExampleDataset'}}}
            elif '下一步行动' in system:answer={'action':'search','queries':['unrelated ranking that must not run']}
            elif '按问题读取论文' in system:
                item=json.loads(body['messages'][-1]['content'])
                suffix=item['metadata']['id'].rsplit(':',1)[1]
                value={'a':.34,'b':.35}[suffix]
                quote=f'Model{suffix} | ExampleDataset | EPE | {value}'
                answer={'method_summary':'本地模拟论文结果','evidence':[{'quote':quote}],
                        'benchmark_results':[{'model':'Model'+suffix,'dataset':'ExampleDataset','metric':'EPE',
                            'value':value,'group':'精度','row_role':'proposed','protocol':'same split','evidence_indices':[1]}]}
            elif '核对每个原表行' in system:
                rows=json.loads(body['messages'][-1]['content'])['rows']
                answer={'row_reviews':[{'row_id':r['row_id'],'scope_match':True,'group_verified':True,
                                        'group':'精度','comparison_key':'same split'} for r in rows]}
            elif '检查报告' in system:answer={'pass':False,'issues':['模拟初稿，使用有引用的部分报告交付']}
            else:answer='片段里没有结果表，需要核对原文。'
            content=answer if isinstance(answer,str) else json.dumps(answer,ensure_ascii=False)
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            chunk={'id':'fixture','object':'chat.completion.chunk','created':1,'model':'test',
                   'choices':[{'index':0,'delta':{'content':content},'finish_reason':'stop'}]}
            self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only');monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    app,_=build_app(tmp_path)
    async def seed():
        app.state.db.execute("INSERT INTO sessions(id,workspace_id,title) VALUES('follow-up','fixture','ExampleDataset')")
        app.state.db.execute('INSERT INTO messages(id,session_id,role,content) VALUES(?,?,?,?)',
                             ('original','follow-up','user','ExampleDataset 的最低 EPE 是多少？'))
        for suffix,value in [('a',.34),('b',.35)]:
            import fitz
            path=tmp_path/'papers/manual'/(suffix+'.pdf');path.parent.mkdir(parents=True,exist_ok=True)
            with fitz.open() as doc:
                for i in range(12):
                    page=doc.new_page()
                    page.insert_text((30,50),'\n'.join(['Synthetic local fixture, not a published result.']*25),fontsize=8)
                    if i==11:page.insert_text((30,520),f'Model{suffix} | ExampleDataset | EPE | {value}',fontsize=10)
                doc.save(path)
            await app.state.kb.upsert_paper({'id':'fixture:'+suffix,'title':'Model'+suffix+' synthetic paper',
                'year':2026,'publication_date':'2026-02-01','retrieval_status':'downloaded','fulltext_path':str(path)})
    def wait(client,identifier):
        for _ in range(500):
            job=client.get('/api/jobs/'+identifier).json()
            if job['status'] in {'succeeded','failed'}:break
            time.sleep(.1)
        assert job['status']=='succeeded',job
    try:
        asyncio.run(seed())
        with TestClient(app,headers={'X-Reader-Client':'desktop'}) as client:
            assert client.post('/api/settings',json={'base_url':f'http://127.0.0.1:{server.server_port}/v1',
                'fast_model':'test','reasoning_model':'test','deepseek_api_key':'test-only'}).status_code==200
            response=client.post('/api/chat',json={'mode':'report_chat','session_id':'follow-up',
                'message':'这两篇都比0.36低','selected_paper_ids':['fixture:a','fixture:b']})
            assert response.status_code==202,response.text
            wait(client,response.json()['job_id'])
            prompt=received[-1]['messages'][-1]['content']
            assert 'Modela | ExampleDataset | EPE | 0.34' in prompt and 'Modelb | ExampleDataset | EPE | 0.35' in prompt
            response=client.post('/api/chat',json={'mode':'report_chat','session_id':'follow-up',
                'message':'那这两篇各是多少？','selected_paper_ids':[]})
            assert response.status_code==202,response.text
            wait(client,response.json()['job_id'])
            prompt=received[-1]['messages'][-1]['content']
            assert 'Modela | ExampleDataset | EPE | 0.34' in prompt and 'Modelb | ExampleDataset | EPE | 0.35' in prompt
            response=client.post('/api/chat',json={'mode':'analysis','session_id':'follow-up',
                'message':short,'selected_paper_ids':[]})
            assert response.status_code==202,response.text
            wait(client,response.json()['job_id'])
            turns=client.get('/api/sessions/follow-up/messages').json()['messages']
            user=next(m for m in turns if m['role']=='user' and m['content']==short)
            snapshot=json.loads(app.state.db.fetchone('SELECT metadata_json FROM messages WHERE id=?',(user['id'],))['metadata_json'])['context_snapshot']
            assert set(snapshot['paper_ids'])=={'fixture:a','fixture:b'}
            assert '这两篇都比0.36低' in snapshot['recent_context']
            assert '片段里没有结果表' in snapshot['recent_context']
            direction=next(b for b in received if '拆解研究想法' in b['messages'][0]['content'])
            prompt=direction['messages'][-1]['content']
            assert 'Modela synthetic paper' in prompt and 'Modelb synthetic paper' in prompt
            assert '这两篇都比0.36低' in prompt and short in prompt
            extracted=[json.loads(b['messages'][-1]['content'])['metadata']['id'] for b in received
                       if '按问题读取论文' in b['messages'][0]['content']]
            assert sorted(extracted)==['fixture:a','fixture:b']
            assistant=app.state.db.fetchone("SELECT metadata_json FROM messages WHERE session_id='follow-up' AND role='assistant' ORDER BY rowid DESC LIMIT 1")
            meta=json.loads(assistant['metadata_json'])
            assert meta['papers_found']==2 and meta['innovations_extracted']==2
            assert meta['reading_coverage']['completed']==2 and meta['unprocessed_count']==0
            report=(tmp_path/meta['report_path']).read_text(encoding='utf-8')
            assert '0.34 EPE' in report and 'Modela' in report
            checkpoints=[json.loads(p.read_text(encoding='utf-8')) for p in (tmp_path/'.agent_history/research').glob('adaptive_*.json')]
            assert len(checkpoints)==1
            assert [d['action'] for d in checkpoints[0]['decisions']]==['read','analyze','deliver']
            assert len([b for b in received if '下一步行动' in b['messages'][0]['content']])==2
    finally:
        server.shutdown();server.server_close()


def test_native_exclusive_owner_and_hundred_completions(tmp_path):
    path=tmp_path/'tasks.db'
    owner=Native(path)
    try:
        other=Native(path)
        assert other.p.wait(timeout=5)!=0
        other.p.stdin.close();other.p.stdout.close();other.p.stderr.close()
        for i in range(100):
            assert owner.submit(id=f'j{i}',key=f'k{i}')['ok']
            job=owner.call('claim')['data']
            assert owner.call('finish',id=job['id'],token=job['token'],status='succeeded')['ok']
        assert len(owner.call('list')['data'])==100
    finally: owner.close()


def test_parent_pipe_eof_stops_worker(tmp_path):
    import sys
    executable = os.getenv('READER_TEST_DESKTOP_EXE')
    command = [executable,'--worker',str(tmp_path)] if executable else [sys.executable,'-m','src.runtime.worker',str(tmp_path)]
    worker=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                            **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    worker.stdin.write(b'{"session_id":"s","message_id":"m","message":"test"}\n')
    worker.stdin.flush();worker.stdin.close()
    assert worker.wait(timeout=10)==3
    worker.stdout.close();worker.stderr.close()


def test_upload_guard_and_private_preview(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from src.app.factory import build_app
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    app,_=build_app(tmp_path)
    with TestClient(app,headers={'X-Reader-Client':'desktop'}) as client:
        (tmp_path/'.secrets.json').write_text('{}')
        assert client.get('/api/workspace/file',params={'path':'.secrets.json'}).status_code==403
        assert client.post('/api/files/import',files={'file':('fake.pdf',b'not a PDF')}).status_code==400
        assert not list((tmp_path/'papers/manual').glob('*.pdf'))


def test_closed_workspace_backup(tmp_path):
    from src.workspace.manager import WorkspaceManager
    from scripts.backup_workspace import backup
    source=tmp_path/'source';WorkspaceManager.create_workspace(source)
    owner=Native(source/'db/tasks.db')
    try:
        assert owner.call('ping')['ok']
        with pytest.raises((RuntimeError,BlockingIOError)):
            backup(source,tmp_path/'blocked')
    finally:owner.close()
    target=backup(source,tmp_path/'backup')
    assert (target/'config.yaml').read_bytes()==(source/'config.yaml').read_bytes()
