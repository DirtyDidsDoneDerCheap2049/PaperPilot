"""Synthetic multi-paper workflow coverage; not a scientific-quality evaluation."""
import asyncio
import json
import pytest


@pytest.mark.parametrize('limit,expected', [(50,50),(12,12)])
def test_fifty_papers_are_accounted_for_in_actual_pipeline(tmp_path, monkeypatch, limit, expected):
    import pymupdf
    from src.app.factory import build_app
    from src.agents.base import AgentContext
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    app, config=build_app(tmp_path)
    config.setdefault('research',{})['followup_rounds']=0
    async def fixture_report(*args):return '# Offline multi-paper fixture\n\nResearch conclusion for plumbing verification.'
    monkeypatch.setattr('src.analysis.reader_report.write_reader_report',fixture_report)
    config['search']['deep_parse_top_k']=limit
    quote='We combine multimodal feature streams through cross attention without a teacher or distillation loss.'
    papers=[]
    for i in range(50):
        path=tmp_path/f'papers/manual/p{i}.pdf'
        doc=pymupdf.open();page=doc.new_page()
        page.insert_textbox(pymupdf.Rect(40,40,550,760),'Synthetic fixture, not published.\nMethods\n'+(quote+'\n')*8,fontsize=11)
        doc.save(path);doc.close()
        papers.append({'id':f'fixture:{i:02d}','title':f'Synthetic multimodal paper {i:02d}',
                       'abstract':quote,'authors':['Offline fixture'],'local_pdf_path':f'papers/manual/p{i}.pdf'})
    class Model:
        reasoning_model='fixture'
        def __init__(self):self.batches=[]
        async def chat_json(self,messages,model=None,*,purpose='regular'):
            system=messages[0]['content']
            if '拆解研究想法' in system:
                return {'method_component':'feature_extraction','method_category':'attention_mechanism',
                        'method_subcategory':'cross_attention','search_queries':['multimodal feature fusion']}
            if '实现层面的创新点' in system:
                return {'is_relevant':True,'method_component':'feature_extraction',
                        'innovation_detail':'Cross attention feature fusion','confidence':0.7,
                        'evidence':[{'section':'Methods','quote':quote,'supports':'Feature fusion'}]}
            if 'paper_assessments' in messages[-1]['content']:
                payload=json.JSONDecoder().raw_decode(messages[-1]['content'])[0]
                profiles=payload['profiles']
                self.batches.append([p['paper_id'] for p in profiles])
                return {'paper_assessments':[{'paper_id':p['paper_id'],'method_relations':['fusion'],
                         'coverage_level':'functional','claim_assessments':[]} for p in profiles]}
            return {'gaps':[],'matrix':{}}
    model=Model();orch=app.state.orchestrator;orch.llm=model
    async def search(*args,**kwargs):return papers
    monkeypatch.setattr(orch,'_search',search)
    events=[]
    class Events:
        async def broadcast(self,session,event):events.append(event)
    orch.ws_manager=Events()
    async def execute():
        try:
            return await orch.run(AgentContext(workspace_id=app.state.workspace_id,session_id='many',workspace_root=tmp_path,config=config),
                                  {'message':'研究多模态模型中的交叉注意力特征融合','selected_paper_ids':[p['id'] for p in papers]})
        finally:await app.state.llm.close()
    try:
        result=asyncio.run(execute())
        assert result.status=='completed',result.error
        assert result.data['innovations_extracted']==expected
        ledger=result.data['paper_processing']
        assert {p['paper_id'] for p in ledger}=={p['id'] for p in papers}
        assert len([p for p in ledger if p['status']=='not_analyzed'])==50-expected
        assert result.data['unprocessed_count']==50-expected
        assert len([pid for batch in model.batches for pid in batch])==expected
        assert len([p for p in ledger if p['status']=='fulltext'])==expected
        assert all(len(batch)<=8 for batch in model.batches)
        assert any(e.get('detail',{}).get('completed')==expected for e in events)
        report=result.data['report_content']
        assert 'fixture:49' not in report
        audit=json.loads(next((tmp_path/'.agent_history/research').glob('*.json')).read_text(encoding='utf-8'))
        assert len(audit['analysis']['paper_processing'])==50
        assert len(orch._session_paper_ids('many'))==50
        for row in app.state.db.fetchall('SELECT profile_json FROM innovation_profiles'):
            profile=json.loads(row['profile_json'])
            assert profile['evidence'][0]['source_verified']
        assert result.data['gaps_count']==0
    finally:
        app.state.vs.close();app.state.db.close()


def test_research_budget_settings_are_applied(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from src.app.factory import build_app
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    app,_=build_app(tmp_path)
    with TestClient(app,headers={'X-Reader-Client':'desktop'}) as client:
        assert client.post('/api/settings',json={'search_deep_parse_top_k':60,'research_max_model_calls':320,'research_timeout_minutes':180}).status_code==200
        settings=client.get('/api/settings').json()
        assert settings['search']['deep_parse_top_k']==60
        assert settings['research']=={'max_model_calls':320,'timeout_minutes':180}
        assert app.state.llm.max_calls==320
        app.state.llm.calls=320
        with pytest.raises(RuntimeError,match='320'):app.state.llm._reserve_call()
        assert client.post('/api/settings',json={'research_max_model_calls':0}).status_code==422
        response=client.post('/api/chat',json={'message':'compare','mode':'report_chat','selected_paper_ids':[str(i) for i in range(9)]})
        assert response.status_code==400 and '研究 Agent' in response.json()['detail']
