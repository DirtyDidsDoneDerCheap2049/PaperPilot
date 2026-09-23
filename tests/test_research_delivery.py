import asyncio
import json
import zipfile
from types import SimpleNamespace as NS
import pytest
from src.parsing.innovation_extractor import InnovationExtractor
from src.analysis.paper_ledger import paper_ledger
from src.analysis.report_text import readable_text
from src.llm.deepseek_client import DeepSeekClient


def test_list_shaped_model_prose_is_preserved_without_weakening_quotes():
    profile=InnovationExtractor._validate({'method_component':['teacher matching','scale alignment'],
        'ablation_insights':[], 'evidence':[{'quote':'An exact original sentence.'}]},'p')
    assert profile['method_component']=='teacher matching\nscale alignment'
    assert profile['ablation_insights']==''
    assert profile['evidence'][0]['quote']=='An exact original sentence.'
    assert InnovationExtractor._validate({'limitations':'Only one dataset'},'p')['limitations']==['Only one dataset']
    with pytest.raises(ValueError):InnovationExtractor._validate({'limitations':{'invalid':'object'}},'p')
    with pytest.raises(ValueError):InnovationExtractor._validate({'evidence':[{'quote':['not a quote']}]},'p')
    with pytest.raises(ValueError):InnovationExtractor._validate({'method_component':{'invented':'object'}},'p')


def test_background_note_is_not_a_successful_fulltext_paper():
    row=paper_ledger([{'id':'prior:note','title':'Background'}],
        [{'paper_id':'prior:note','has_fulltext':True,'source_type':'prior_knowledge'}],
        [{'paper_id':'prior:note','assessment_status':'audited'}])[0]
    assert row['status']=='excluded'
    failed=paper_ledger([{'id':'p'}],[{'paper_id':'p','error':'schema validation failed'}],[])[0]
    assert failed['status']=='extraction_failed' and 'schema validation' in failed['reason']


def test_model_summary_is_readable_and_not_python_repr():
    text=readable_text({'scope':'Only this corpus','key_findings':['A','B']})
    assert '核查范围：Only this corpus' in text and '- A' in text
    assert "{'" not in text


def test_generic_vendor_request_controls(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    llm=DeepSeekClient(base_url='https://vendor.example/v1',fast_model='vendor-model',
        capability_profile='compatible',token_parameter='max_completion_tokens',json_output='prompt',
        extra_body_params={'reasoning_effort':'high'})
    options=llm.request_options('vendor-model',structured=True)
    assert 'max_completion_tokens' in options and 'max_tokens' not in options
    assert 'response_format' not in options and options['extra_body']=={'reasoning_effort':'high'}
    llm.configure(token_parameter='omit',extra_body_params={})
    assert llm.request_options('vendor-model')=={'model':'vendor-model'}


def test_source_error_is_not_a_successful_empty_search(monkeypatch):
    import src.tools.search_tools as search
    async def limited(*args,**kwargs):raise RuntimeError('private diagnostic')
    async def empty(*args,**kwargs):return []
    monkeypatch.setattr(search,'search_semantic_scholar',limited)
    monkeypatch.setattr(search,'search_arxiv',empty)
    monkeypatch.setattr(search,'search_crossref',empty)
    result=asyncio.run(search.search_all('test'))
    assert result==[]
    assert [r['status'] for r in result.diagnostics]==['failed','ok','ok']
    assert 'private diagnostic' not in json.dumps(result.diagnostics)


def test_handoff_scope_redaction_and_complete_json(tmp_path,monkeypatch):
    from src.runtime.handoff import write_handoff
    from src.server.routes_workspace import _latest_gap_row,GptHandoffRequest
    from src.knowledge.sqlite_store import SQLiteStore
    db=SQLiteStore(tmp_path/'test.db');db.init_schema()
    db.execute('INSERT INTO gap_analyses(id,session_id,direction) VALUES(?,?,?)',('g','other','{}'))
    assert _latest_gap_row(db,tmp_path,GptHandoffRequest(session_id='empty-session')) is None
    monkeypatch.setattr('src.runtime.handoff.read_secrets',lambda _: {'key':'private-test-key'})
    output=tmp_path/'exports'/'review.md';output.parent.mkdir()
    result=write_handoff(tmp_path,output,'private-test-key '+str(tmp_path/'papers'/'paper.pdf'),
        {'large':'x'*125000},{'pdf_files':[str(tmp_path/'papers'/'paper.pdf')]})
    with zipfile.ZipFile(tmp_path/result['zip_path']) as archive:
        assert len(archive.namelist())==3
        for name in archive.namelist():
            text=archive.read(name).decode()
            assert 'private-test-key' not in text and str(tmp_path) not in text
            if name.endswith('.context.json'):assert len(json.loads(text)['large'])==125000
    db.close()


@pytest.mark.parametrize('parameter',['max_tokens','max_completion_tokens'])
def test_vendor_chat_json_and_stream_on_mock_transport(monkeypatch,parameter):
    import httpx
    from openai import AsyncOpenAI
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    requests=[]
    def handle(request):
        payload=json.loads(request.content);requests.append(payload)
        assert payload['model']=='vendor-model' and payload[parameter]==8192
        assert 'thinking' not in payload and 'reasoning_effort' not in payload
        if payload['stream']:
            event={'id':'response','object':'chat.completion.chunk','created':1,'model':'vendor-model','choices':[{'index':0,'delta':{'content':'verified'},'finish_reason':'stop'}]}
            return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n')
        return httpx.Response(200,json={'id':'response','object':'chat.completion','created':1,'model':'vendor-model','choices':[{'index':0,'message':{'role':'assistant','content':'{"ok":true}'},'finish_reason':'stop'}]})
    async def run():
        llm=DeepSeekClient(base_url='https://vendor.example/v1',fast_model='vendor-model',max_output_tokens=8192,token_parameter=parameter,capability_profile='compatible')
        llm.client=AsyncOpenAI(api_key='fixture-only',base_url='https://vendor.example/v1',http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        llm._ready=True
        try:
            assert await llm.chat_json([{'role':'user','content':'Return JSON'}])=={'ok':True}
            assert ''.join([part async for part in llm.chat_stream([{'role':'user','content':'hello'}])])=='verified'
        finally:await llm.close()
    asyncio.run(run());assert len(requests)==2


def test_scoped_missing_history_and_export_download(tmp_path,monkeypatch):
    from src.app.factory import build_app
    from fastapi.testclient import TestClient
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    app,_=build_app(tmp_path)
    db=app.state.db
    db.execute("INSERT INTO sessions(id,workspace_id,title) VALUES('s','w','Research')")
    for pid in ('inside','outside'):
        db.execute("INSERT INTO papers(id,title,retrieval_status) VALUES(?,?,'missing_fulltext')",(pid,pid))
    db.execute("INSERT INTO gap_analyses(id,session_id,direction,search_log_json) VALUES('g','s','{}',?)",(json.dumps({'papers':[{'id':'inside'}]}),))
    db.execute("INSERT INTO agent_runs(id,session_id,agent_name,status,input_json,output_json) VALUES('r','s','SearchAgent','completed','{}','{}')")
    client=TestClient(app,headers={'X-Reader-Client':'desktop'})
    assert [p['id'] for p in client.get('/api/missing-papers?session_id=s').json()['papers']]==['inside']
    assert client.get('/api/sessions/s/agent-runs').json()['runs'][0]['status']=='completed'
    response=client.post('/api/export/gpt-handoff',json={'session_id':'s'})
    assert response.status_code==200
    path=response.json()['zip_path']
    download=client.get('/api/export/download',params={'path':path})
    assert download.status_code==200 and download.content.startswith(b'PK')
    assert client.get('/api/export/download',params={'path':'config.yaml'}).status_code==404
    assert client.get('/api/export/download',params={'path':'../outside.zip'}).status_code==403
    db.close()
