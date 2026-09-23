"""Synthetic material checks plumbing, not research accuracy."""
import asyncio
import json
from pathlib import Path


def test_local_pdf_to_report_without_external_services(tmp_path, monkeypatch):
    import pymupdf
    from src.app.factory import build_app
    from src.agents.base import AgentContext
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    app,config=build_app(tmp_path)
    config.setdefault('research',{})['followup_rounds']=0
    async def fixture_report(*args):return '# Offline fixture\n\nResearch conclusion for plumbing verification.'
    monkeypatch.setattr('src.analysis.reader_report.write_reader_report',fixture_report)
    quote='We fuse two multimodal feature streams through cross attention without knowledge distillation.'
    pdf=tmp_path/'papers/manual/synthetic.pdf'
    doc=pymupdf.open();page=doc.new_page()
    page.insert_textbox(pymupdf.Rect(40,40,550,760),
        'Synthetic offline fixture. Not a published paper.\nMethods\n'+
        (quote+'\n')*12,fontsize=11)
    doc.save(pdf);doc.close()
    paper={'id':'synthetic:p1','title':'Synthetic multimodal fusion fixture','abstract':quote,
           'authors':['Test fixture'],'year':2026,'local_pdf_path':'papers/manual/synthetic.pdf'}
    class Model:
        reasoning_model='offline-fixture'
        async def chat_json(self,messages,model=None,*,purpose='regular'):
            system=messages[0]['content']
            if '拆解研究想法' in system:
                return {'method_component':'feature_extraction','method_category':'attention_mechanism',
                        'method_subcategory':'cross_attention','search_queries':['multimodal feature fusion']}
            if '实现层面的创新点' in system:
                return {'is_relevant':True,'method_component':'feature_extraction',
                        'innovation_detail':'Cross attention feature fusion',
                        'evidence':[{'section':'Methods','quote':quote,'supports':'Feature fusion'}],
                        'confidence':0.7}
            # Simulate incomplete audit output. Reliability gates must degrade.
            return {}
    async def search(*args,**kwargs):return [paper]
    orch=app.state.orchestrator;orch.llm=Model()
    monkeypatch.setattr(orch,'_search',search)
    async def execute():
        try:
            return await orch.run(AgentContext(workspace_id=app.state.workspace_id,
                session_id='offline',workspace_root=tmp_path,config=config),
                {'message':'比较多模态模型特征融合','session_id':'offline','message_id':'fixture-message'})
        finally:await app.state.llm.close()
    try:
        result=asyncio.run(execute())
        assert result.status=='completed',result.error
        assert result.data['innovations_extracted']==1
        assert result.data['gaps_count']==0
        assert Path(result.data['report_path']).is_file()
        saved=app.state.db.fetchone("SELECT profile_json FROM innovation_profiles WHERE paper_id='synthetic:p1'")
        profile=json.loads(saved['profile_json'])
        assert profile['has_fulltext'] and profile['evidence'][0]['source_verified']
        assert profile['evidence'][0]['source_span']['sha256']
        assert app.state.db.fetchone("SELECT id FROM gap_analyses WHERE session_id='offline'")
        assert len(app.state.db.fetchall("SELECT id FROM messages WHERE session_id='offline'"))==2
    finally:
        app.state.vs.close();app.state.db.close()
