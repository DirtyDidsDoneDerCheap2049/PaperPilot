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
            chunk={'id':'test','object':'chat.completion.chunk','created':1,'model':'test','choices':[{'index':0,'delta':{'content':'离线测试回答：请核查原文。'},'finish_reason':None}]}
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
            messages=client.get('/api/sessions/test-session/messages').json()['messages']
            assert len(messages)==2
            assert '离线测试回答' in messages[-1]['content']
            assert client.get(f'/api/jobs/{id}/events').json()['events']
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
    worker=subprocess.Popen([sys.executable,'-m','src.runtime.worker',str(tmp_path)],
                            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
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
