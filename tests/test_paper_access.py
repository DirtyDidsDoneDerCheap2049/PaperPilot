import json
import pytest
from src.knowledge.paper_access import missing_papers,source_links
from src.knowledge.sqlite_store import SQLiteStore


@pytest.fixture
def db(tmp_path):
    value=SQLiteStore(tmp_path/'db/ai_reader.db');value.init_schema()
    yield value
    value.close()


def add(db,pid,**fields):
    data={'id':pid,'title':'Paper '+pid,'retrieval_status':'metadata_only',**fields}
    db.execute('INSERT INTO papers('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')',tuple(data.values()))


def test_scope_priority_duplicate_fulltext_and_readonly(db,tmp_path):
    (tmp_path/'full.pdf').write_bytes(b'%PDF-1.4\noriginal')
    for pid in ('needed','alias','library','available','available-alias','prior:note','hidden','stale'):add(db,pid)
    db.execute("UPDATE papers SET retrieval_status='off_topic' WHERE id='hidden'")
    db.execute("UPDATE papers SET fulltext_path='full.pdf',retrieval_status='downloaded' WHERE id='available-alias'")
    db.execute("UPDATE papers SET parsed_markdown_path='gone.md',retrieval_status='parsed' WHERE id='stale'")
    for pid,root in [('needed','needed'),('alias','needed'),('available','available'),('available-alias','available')]:
        db.execute('INSERT INTO paper_organization(paper_id,canonical_id,category,tags_json,summary,run_id) VALUES(?,?,?,\'[]\',\'\',\'r\')',(pid,root,'主题'))
    db.execute("INSERT INTO sessions(id,workspace_id,title) VALUES('s','w','Research')")
    db.execute("INSERT INTO gap_analyses(id,session_id,direction,search_log_json,evidence_json) VALUES('g','s','{}',?,'[]')",(json.dumps({'papers':[{'id':'alias'}],'selected_paper_ids':['alias']}),))
    db.execute("INSERT INTO gap_analyses(id,session_id,direction,search_log_json,evidence_json) VALUES('checkpoint_later','s','{}',?,'[]')",(json.dumps({'papers':[{'id':'library'}]}),))
    original=db.fetchall('SELECT * FROM papers ORDER BY id')
    result=missing_papers(db,tmp_path)
    assert [p['id'] for p in result['papers']]==['needed']
    assert result['papers'][0]['usage_priority']=='selected' and result['papers'][0]['duplicate_count']==1
    assert result['summary']=={'missing_total':3,'research_total':1,'library_only_total':2,'raw_missing_records':4,'folded_records':1}
    entire=missing_papers(db,tmp_path,'workspace')
    assert {p['id'] for p in entire['papers']}=={'needed','library','stale'}
    assert missing_papers(db,tmp_path,'session','unknown')['total']==0
    assert missing_papers(db,tmp_path,'workspace',q='library')['total']==1
    assert missing_papers(db,tmp_path,'workspace',limit=1,offset=1)['count']==1
    assert db.fetchall('SELECT * FROM papers ORDER BY id')==original


def test_latest_research_scope_does_not_promote_old_search_results(db,tmp_path):
    add(db,'old');add(db,'new');add(db,'selected')
    db.execute("INSERT INTO sessions(id,workspace_id,title) VALUES('s','w','Research')")
    for gid,pid in [('g1','old'),('g2','new')]:
        db.execute("INSERT INTO gap_analyses(id,session_id,direction,search_log_json,evidence_json) VALUES(?,'s','{}',?,'[]')",(gid,json.dumps({'papers':[{'id':pid}],'selected_paper_ids':['selected']})))
    result=missing_papers(db,tmp_path,'session','s')
    assert [p['id'] for p in result['papers']]==['selected','new']
    assert result['summary']['library_only_total']==1


def test_source_links_use_identifiers_encode_titles_and_reject_unsafe_urls():
    links=source_links({'id':'doi:10.123/example','title':'<script> & "A"','url':'javascript:alert(1)',
                        'open_access_pdf_url':'https://user:secret@example.com/file.pdf'},
                       [{'arxiv_id':'2501.12345v2','url':'https://openaccess.thecvf.com/content/paper.html'}])
    urls=[p['url'] for p in links]
    assert 'https://doi.org/10.123/example' in urls
    assert 'https://arxiv.org/abs/2501.12345v2' in urls
    assert any(p['label']=='会议原文页' for p in links)
    assert not any('secret' in u or u.startswith('javascript:') for u in urls)
    assert next(u for u in urls if 'scholar.google' in u).endswith('%22%3Cscript%3E+%26+%22A%22%22')


def test_missing_api_preserves_compatibility_and_checks_scope(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from src.app.factory import build_app
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    app,_=build_app(tmp_path);db=app.state.db
    add(db,'library')
    client=TestClient(app,headers={'X-Reader-Client':'desktop'})
    assert client.get('/api/missing-papers').json()['total']==1
    assert client.get('/api/missing-papers?scope=research').json()['total']==0
    assert client.get('/api/missing-papers?scope=session').json()['papers']==[]
    assert client.get('/api/missing-papers?scope=unknown').status_code==400
    db.close()
