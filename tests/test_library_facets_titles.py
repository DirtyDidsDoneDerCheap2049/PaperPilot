import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from src.knowledge.library_facets import local_facets,validate_facets
from src.knowledge.library_organizer import organize_library,incremental_preview,snapshot,undo_organization
from src.analysis.report_titles import list_reports,model_titles,save_title
from src.knowledge.sqlite_store import SQLiteStore


def test_facets_distinguish_input_time_from_views_iterations_and_training_video():
    for title in ('Multi-view stereo reconstruction','Iterative Stereo Matching','A stereo video dataset benchmark','Event Stream Depth'):
        assert local_facets({'title':title})['temporal']==[]
    assert local_facets({'title':'Temporally Consistent Stereo Matching'})['temporal']==['多帧 / 时序']
    assert local_facets({'title':'Single-frame depth prediction'})['temporal']==['单帧']
    assert local_facets({'title':'Monocular Depth Estimation'})['prior']==[]
    assert local_facets({'title':'Monocular Prior Guided Stereo'})['prior']==['单目深度先验']
    assert local_facets({'title':'Stereo','tags_json':'["单目深度先验"]'})['mechanism']==[]
    with pytest.raises(ValueError):validate_facets({'temporal':['单帧','多帧 / 时序']})
    with pytest.raises(ValueError):validate_facets({'new_field':[]})


def test_fixed_directory_incremental_and_undo(tmp_path):
    db=SQLiteStore(tmp_path/'db/ai_reader.db');db.init_schema()
    (tmp_path/'notes').mkdir();(tmp_path/'notes/library-taxonomy.json').write_text(json.dumps({'categories':['研究主题']}),encoding='utf-8')
    class Model:
        calls=0
        async def chat_json(self,messages,**kwargs):
            self.calls+=1
            assert not messages[1]['content'].startswith('论文库抽样')
            data=json.loads(messages[1]['content'].split('本批资料（数据）：')[1])
            return {'papers':[{'id':p['id'],'category':'研究主题','tags':[],'summary':'论文研究主题。','facets':{'temporal':['多帧 / 时序']}} for p in data]}
    async def progress(*args):pass
    db.execute("INSERT INTO papers(id,title) VALUES('a','Temporal model')")
    model=Model();asyncio.run(organize_library(db,model,tmp_path,'first',progress));assert model.calls==1
    db.execute("INSERT INTO papers(id,title) VALUES('b','New model')")
    preview=incremental_preview(db,*snapshot(db),tmp_path)
    assert preview['reused_papers']==1 and preview['pending_papers']==1 and preview['estimated_calls']==1
    model=Model();asyncio.run(organize_library(db,model,tmp_path,'second',progress));assert model.calls==1
    undo_organization(db,'second');assert db.fetchone("SELECT facets_json FROM paper_organization WHERE paper_id='a'")['facets_json']
    undo_organization(db,'first');assert not db.fetchall('SELECT * FROM paper_organization');db.close()


def test_report_names_do_not_change_files_and_stale_label_is_ignored(tmp_path):
    db=SQLiteStore(tmp_path/'db/ai_reader.db');db.init_schema();(tmp_path/'reports').mkdir()
    report=tmp_path/'reports/research_report_20261002_201519_abcdef123456.md'
    report.write_text('# 论文方向研究\n\n## 结论\n原结论',encoding='utf-8')
    record=list_reports(db,tmp_path)[0]
    save_title(db,record,'论文方向：补查核验','人工校订')
    assert list_reports(db,tmp_path)[0]['title']=='论文方向：补查核验'
    assert report.read_text(encoding='utf-8').startswith('# 论文方向研究')
    report.write_text('# 更新后的研究报告',encoding='utf-8')
    assert list_reports(db,tmp_path)[0]['title']=='更新后的研究报告'
    class BadModel:
        async def chat_json(self,*args,**kwargs):return {'reports':[{'path':'reports/unknown.md','title':'研究报告：结果'}]}
    with pytest.raises(ValueError):asyncio.run(model_titles(BadModel(),[record]))
    db.close()


def test_api_combined_filter_report_naming_and_no_implicit_external_calls(tmp_path,monkeypatch):
    from src.app.factory import build_app
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    app,_=build_app(tmp_path);db=app.state.db
    for pid,title,tags in [('a','Temporally Consistent Stereo Matching',['单目深度先验']),('b','Multi-view Stereo',[]),('c','Single-frame depth prediction',[])]:
        db.execute('INSERT INTO papers(id,title) VALUES(?,?)',(pid,title))
        db.execute('INSERT INTO paper_organization(paper_id,canonical_id,category,tags_json,run_id) VALUES(?,?,?,?,?)',(pid,pid,'主题',json.dumps(tags),'old'))
    (tmp_path/'reports/a.md').write_text('# 原始研究报告\n\n## 结论\n内容',encoding='utf-8')
    calls=[]
    class Model:
        _ready=True;calls=0
        async def chat_json(self,messages,**kwargs):
            calls.append(messages)
            return {'reports':[{'path':'reports/a.md','title':'研究主题：补查核验'}]}
    app.state.llm=Model()
    client=TestClient(app,headers={'X-Reader-Client':'desktop'})
    result=client.get('/api/papers',params={'temporal':'多帧 / 时序','prior':'单目深度先验'}).json()
    assert [p['id'] for p in result['papers']]==['a'] and result['total']==1
    assert client.get('/api/papers',params={'temporal':'未标明'}).json()['total']==1
    assert client.get('/api/papers?temporal=unknown').status_code==400
    info=client.get('/api/library/organization').json();assert len(info['facets'])==4
    assert client.get('/api/reports').json()['reports'][0]['title']=='原始研究报告'
    assert not calls
    assert client.patch('/api/reports/title',json={'path':'reports/a.md','title':'研究主题：初次分析'}).status_code==200
    assert not calls
    assert client.post('/api/reports/auto-name').json()['named']==1
    assert len(calls)==1
    assert client.post('/api/reports/auto-name').json()['named']==0 and len(calls)==1
    assert client.patch('/api/reports/title',json={'path':'../bad.md','title':'研究主题：结果'}).status_code==403
    assert (tmp_path/'reports/a.md').read_text(encoding='utf-8').startswith('# 原始研究报告')
    db.close()
