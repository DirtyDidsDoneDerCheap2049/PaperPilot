"""Synthetic regressions for planning and evidence boundaries, not an accuracy claim."""
import asyncio
import json
from datetime import date
from types import SimpleNamespace
import pytest
from src.analysis.research_plan import normalize_plan, resolve_time_scope, publication_scope
from src.analysis.question_evidence import numerical_rows, summarize_benchmarks
from src.analysis.provenance import bind_profile


TODAY=date(2026,10,2)


def test_relative_year_ignores_model_cutoff_and_handles_leap_day():
    assert resolve_time_scope('最近一年',{'start':'2024-01-01','end':'2025-12-31'},TODAY)['start']=='2025-10-02'
    assert resolve_time_scope('最近一两年',{},TODAY)['start']=='2024-10-02'
    assert resolve_time_scope('past two years',{},date(2024,2,29))['start']=='2022-02-28'
    assert normalize_plan({},'最近一年最低EPE是多少？',TODAY)['task_type']=='benchmark_comparison'
    assert resolve_time_scope('',{'start':'2027-01-01','end':'2028-01-01'},TODAY)=={}
    scope=resolve_time_scope('最近一年',today=TODAY)
    assert publication_scope({'publication_date':'2025-11'},scope)[0]=='in_scope'
    assert publication_scope({'publication_date':'2025-10'},scope)[0]=='date_unconfirmed'


@pytest.mark.parametrize('paper,expected',[
    ({'publication_date':'2025-10-01'},'outside_scope'),
    ({'publication_date':'2025-10-02'},'in_scope'),
    ({'publication_date':'2026-10-02'},'in_scope'),
    ({'publication_date':'2026-10-03'},'outside_scope'),
    ({'year':2024},'outside_scope'),({'year':2025},'date_unconfirmed'),
    ({'year':2026},'date_unconfirmed'),({},'date_unconfirmed')])
def test_publication_scope_does_not_invent_day_precision(paper,expected):
    assert publication_scope(paper,resolve_time_scope('最近一年',today=TODAY))[0]==expected


def test_arxiv_month_is_not_made_into_an_exact_day_or_later_publication():
    from src.analysis.research_plan import normalize_publication_metadata,paper_arxiv_id
    scope=resolve_time_scope('最近一年',today=TODAY)
    old={'id':'arxiv:2503.00001v2','publication_date':'2025-10-19','date_source':'crossref_published'}
    normalized=normalize_publication_metadata(old)
    assert normalized['publication_date']=='2025-03' and publication_scope(normalized,scope)[0]=='outside_scope'
    assert publication_scope({'arxiv_id':'2512.00001'},scope)[0]=='in_scope'
    assert publication_scope({'arxiv_id':'2510.00001'},scope)[0]=='date_unconfirmed'
    exact=normalize_publication_metadata({'arxiv_id':'2510.00001','publication_date':'2025-10-03','date_source':'arxiv_first_submission'})
    assert exact['publication_date']=='2025-10-03'
    assert paper_arxiv_id({'doi':'10.1234/2503.00001','url':'https://doi.org/10.1234/2503.00001'}) is None
    assert paper_arxiv_id({'arxiv_id':'2513.00001'}) is None


def test_old_sqlite_schema_upgrade_and_local_dates_survive_upserts(tmp_path):
    from src.knowledge.sqlite_store import SQLiteStore,SCHEMA_SQL
    from src.knowledge.kb_manager import KBManager
    db=SQLiteStore(tmp_path/'legacy.db')
    db.conn.executescript(SCHEMA_SQL.replace('publication_date TEXT, date_source TEXT, ',''))
    db.execute('INSERT INTO papers(id,title,year) VALUES(?,?,?)',('p','Preserved old paper',2025))
    db.init_schema();db.init_schema()
    assert db.fetchone('SELECT title FROM papers WHERE id=?',('p',))['title']=='Preserved old paper'
    vector=SimpleNamespace(add_documents=lambda *a:None,query=lambda *a:[SimpleNamespace(id='p',distance=0)])
    kb=KBManager(db,vector)
    async def run():
        await kb.upsert_paper({'id':'p','title':'Preserved old paper','year':2025,'arxiv_id':'2510.00001','publication_date':'2025-10-03','date_source':'arxiv_first_submission'})
        await kb.upsert_paper({'id':'p','title':'Preserved old paper','year':2025,'arxiv_id':'2510.00001'})
        local=await kb.search_related('question')
        assert local[0]['paper']['publication_date']=='2025-10-03'
        assert local[0]['paper']['date_source']=='arxiv_first_submission'
    try:asyncio.run(run())
    finally:db.close()


def test_duplicate_cache_does_not_discard_fresh_dates_and_abstract():
    from src.tools.search_tools import merge_and_deduplicate
    result=asyncio.run(merge_and_deduplicate([
        {'id':'local','title':'Same research paper','year':2025},
        {'id':'remote','title':'Same research paper','publication_date':'2025-12-03','abstract':'New source'}]))
    assert len(result)==1 and result[0].id=='local'
    assert result[0].publication_date=='2025-12-03' and result[0].abstract=='New source'


def test_search_requests_date_filters_for_all_three_providers(monkeypatch):
    import httpx
    import src.tools.search_tools as search
    requests=[]
    def handle(request):
        requests.append(request)
        if 'semanticscholar' in request.url.host:
            return httpx.Response(200,json={'data':[{'paperId':'p','title':'Paper','year':2026,'publicationDate':'2026-02-03'}]})
        if 'arxiv' in request.url.host:
            return httpx.Response(200,text='<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/2602.00001v1</id><title>Paper</title><published>2026-02-03T00:00:00Z</published></entry></feed>')
        return httpx.Response(200,json={'message':{'items':[{'DOI':'test','title':['Paper'],'published':{'date-parts':[[2026,2,3]]}}]}})
    real=httpx.AsyncClient
    monkeypatch.setattr(search.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    scope=resolve_time_scope('最近一年',today=TODAY)
    async def run():
        for function in (search.search_semantic_scholar,search.search_arxiv,search.search_crossref):
            assert (await function('benchmark',time_scope=scope))[0].publication_date=='2026-02-03'
    asyncio.run(run())
    assert requests[0].url.params['publicationDateOrYear']=='2025:2026-10-02'
    assert 'submittedDate:[202510020000 TO 202610022359]' in requests[1].url.params['search_query']
    assert 'from-pub-date:2025-10-02' in requests[2].url.params['filter']


def test_rank_before_cap_and_old_local_papers_do_not_take_recent_slots(monkeypatch):
    from src.agents.orchestrator import Orchestrator
    from src.tools.search_tools import PaperMetadata
    async def search(*a,**kw):
        assert kw['time_scope']['start']=='2025-10-02'
        return [PaperMetadata(id=str(i),title='Synthetic '+str(i),year=2026,publication_date='2026-01-01') for i in range(12)]
    class Model:
        async def chat_json(self,messages,**kw):
            return {'ranked_papers':[{'paper_id':str(i),'relevance_score':i/12} for i in range(12)]}
    async def local(*args):return [{'paper':{'id':'old','title':'Old','year':2020}}]
    monkeypatch.setattr('src.tools.search_tools.search_all',search)
    owner=Orchestrator(None,Model(),None,SimpleNamespace(search_related=local))
    direction={'research_plan':normalize_plan({'task_type':'benchmark_comparison'},'最近一年最低EPE',TODAY),'search_queries':['query']}
    result=asyncio.run(owner._search('question',direction,{'research':{'max_new_papers':2}}))
    assert [p['id'] for p in result]==['11','10']
    assert direction['search_execution']['excluded_by_date'][0]['id']=='old'
    assert '0' in direction['search_execution']['excluded_by_relevance']


def test_optional_runtime_numbers_and_nulls_do_not_discard_valid_rows():
    from src.analysis.question_evidence import QuestionEvidence
    value=QuestionEvidence.model_validate({'method_summary':None,'findings':None,'benchmark_results':[
        {'model':'SyntheticModel','dataset':'ExampleDataset','metric':'EPE','value':.4,'runtime':47,'hardware':None}]})
    assert value.benchmark_results[0].runtime=='47' and value.benchmark_results[0].hardware==''
    assert value.findings==[]
    with pytest.raises(ValueError):
        QuestionEvidence.model_validate({'benchmark_results':[{'model':'S','dataset':'D','metric':'EPE','value':float('nan')}]})


def test_arxiv_first_public_date_is_not_replaced_by_later_revision():
    from src.tools.paper_source import arxiv_page_metadata
    html='<meta name="citation_title" content="Synthetic Paper"><div>Submitted on 7 Mar 2025 (v1), last revised 21 Jul 2026</div>'
    value=arxiv_page_metadata(html,'2503.00001')
    assert value['publication_date']=='2025-03-07' and value['date_source']=='arxiv_first_submission'
    assert publication_scope(value,resolve_time_scope('最近一年',today=TODAY))[0]=='outside_scope'


def test_metadata_rate_limit_retries_once_then_returns_failure(monkeypatch):
    import httpx
    import src.tools.search_tools as search
    waits=[]
    async def sleep(delay):waits.append(delay)
    monkeypatch.setattr(search.asyncio,'sleep',sleep)
    calls=[]
    def handle(request):calls.append(request);return httpx.Response(429,headers={'Retry-After':'1'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assert (await search.metadata_request(client,'https://source.example/search')).status_code==429
    asyncio.run(run());assert len(calls)==2 and waits==[3]


def test_reported_numbers_need_bound_model_row_and_separate_protocol_minima():
    text='SyntheticModel | ExampleDataset | EPE | 0.40\nTeacherModel | ExampleDataset | EPE | 0.10'
    profile=bind_profile({'paper_id':'p','year':2026,'publication_date':'2026-02-01',
        'evidence':[{'quote':text}], 'benchmark_results':[
            {'model':'SyntheticModel','dataset':'ExampleDataset','metric':'EPE','value':0.40,'group':'accuracy','row_role':'proposed','evidence_indices':[1]},
            {'model':'InventedModel','dataset':'ExampleDataset','metric':'EPE','value':0.40,'group':'accuracy','row_role':'proposed','evidence_indices':[1]}]},text,'synthetic.md')
    plan=normalize_plan({'task_type':'benchmark_comparison','comparison':{'groups':['accuracy'],'lower_is_better':True}},'最近一年最低EPE',TODAY)
    rows=numerical_rows([profile],plan)
    assert rows[0]['source_bound'] and not rows[1]['source_bound']
    values=[dict(rows[0],eligible=True,comparison_key='split A'),dict(rows[0],value=0.3,eligible=True,comparison_key='split B')]
    assert [row['value'] for row in summarize_benchmarks(values,plan)[0]['best_reported']]==[0.3]
    assert len(summarize_benchmarks(values,{**plan,'comparison_mode':'controlled_comparison'})[0]['best_reported'])==2
    profile['benchmark_results'][0]['value']=.1
    assert not numerical_rows([profile],plan)[0]['source_bound']


@pytest.mark.parametrize('quote,value,expected',[
    ('ModelA\n0.46\n47',.46,True),
    ('ModelA\n✓\n✓\n**0.45**\n110',.45,True),
    ('ModelA\n0.46 px\n47 ms',.46,True),
    ('ModelA\n0.90\nModelB\n0.45',.45,False),
    ('ModelA\nModelB\n0.90\n0.45',.45,False),
    ('ModelA\nText about another experiment\n0.45',.45,False)])
def test_vertical_cells_bind_without_crossing_another_model(quote,value,expected):
    from src.analysis.question_evidence import model_value_in_quote
    assert model_value_in_quote('ModelA',value,quote)==expected


def test_known_ineligible_rows_do_not_consume_model_review_calls():
    from src.analysis.question_evidence import analyze_question
    text='ModelA | ExampleDataset | EPE | 0.4'
    def profile(pid,role,date_value,model='ModelA'):
        return bind_profile({'paper_id':pid,'publication_date':date_value,'evidence':[{'quote':text}],
            'benchmark_results':[{'model':model,'dataset':'ExampleDataset','metric':'EPE','value':.4,
             'group':'accuracy','row_role':role,'evidence_indices':[1]}]},text,'synthetic.md')
    profiles=[profile('teacher','teacher','2026-02-01'),profile('old','proposed','2024-02-01'),
              profile('unbound','proposed','2026-02-01','InventedModel'),profile('good','proposed','2026-02-01')]
    class Model:
        reasoning_model='fixture';calls=0
        async def chat_json(self,messages,**kw):
            self.calls+=1
            rows=json.loads(messages[-1]['content'])['rows']
            assert [row['paper_id'] for row in rows]==['good']
            return {'row_reviews':[{'row_id':rows[0]['row_id'],'scope_match':True,'group_verified':True,'comparison_key':'same split'}]}
    model=Model()
    plan=normalize_plan({'task_type':'benchmark_comparison','comparison':{'groups':['accuracy']}},'最近一年最低EPE',TODAY)
    result=asyncio.run(analyze_question(model,{'research_plan':plan},profiles))
    assert model.calls==1 and len(result['benchmark_table'])==4
    assert [row['paper_id'] for row in result['benchmark_table'] if row['eligible']]==['good']


def test_checkpoint_continuation_preserves_limits_and_rechecks_without_rereading(tmp_path,monkeypatch):
    from src.analysis.adaptive_research import execute_research
    from src.agents.base import AgentContext
    plan=normalize_plan({'task_type':'benchmark_comparison','comparison':{'groups':['accuracy']}},'最近一年最低EPE',TODAY)
    snapshot={'direction':{'research_plan':plan,'search_execution':{'completed_queries':[{'query':'original query'}],
        'excluded_by_date':[],'deferred_new_records':0,'new_papers':1}},
        'papers':[{'id':'p','title':'Synthetic','publication_date':'2026-02-01'}],
        'innovations':[{'paper_id':'p','method_summary':'Preserved evidence'}],
        'parse_data':{'parsed':['p'],'metadata_only':[],'deferred':[],'failed':[]},
        'decisions':[{'step':1,'action':'search','queries':['original query']},
            {'step':2,'action':'read','paper_ids':['p']},{'step':3,'action':'lookup','paper_ids':['p']}],
        'analysis':{'answer_status':'old analysis must not be reused'}}
    states=[]
    class Model:
        max_calls=22;calls=0
        async def chat_json(self,messages,**kw):
            states.append(json.loads(messages[-1]['content']))
            return {'action':'analyze' if len(states)==1 else 'deliver'}
    async def create(*args,**kw):return 'fixture'
    async def finish(*args,**kw):pass
    async def analyze(llm,direction,profiles):
        assert profiles[0]['method_summary']=='Preserved evidence'
        return {'answer_status':'rechecked','coverage_audit':[]}
    monkeypatch.setattr('src.analysis.question_evidence.analyze_question',analyze)
    def no_selected(*args):raise AssertionError('Checkpoint discarded for a new selected-paper lookup')
    owner=SimpleNamespace(llm=Model(),_selected_papers=no_selected,_parse_budget=lambda *a:40,
        _create_agent_run=create,_finish_agent_run=finish)
    ctx=AgentContext(workspace_root=tmp_path,workspace_id='fixture',session_id='resume',
        config={'research':{'max_new_papers':40,'max_agent_steps':6}})
    papers,profiles,analysis,parsed=asyncio.run(execute_research(owner,ctx,{},[],resume_snapshot=snapshot))
    assert states[0]['searched_queries']==['original query'] and states[0]['looked_up_papers']==['p']
    assert states[0]['papers'][0]['read_attempts']==1 and states[0]['remaining_steps']==3
    assert states[0]['analysis']['answer_status'] is None
    assert parsed['parsed']==['p'] and analysis['answer_status']=='rechecked'
    saved=json.loads(next((tmp_path/'.agent_history/research').glob('adaptive_*.json')).read_text(encoding='utf-8'))
    assert saved['resumed_from_checkpoint'] and [entry['action'] for entry in saved['decisions']]==['search','read','lookup','analyze','deliver']
    assert len(snapshot['decisions'])==3


def test_maintenance_resume_registers_real_question_once(tmp_path):
    from src.knowledge.sqlite_store import SQLiteStore
    from scripts.validate_adaptive_research import ensure_acceptance_session
    db=SQLiteStore(tmp_path/'acceptance.db');db.init_schema()
    state=SimpleNamespace(db=db,workspace_id='fixture')
    attempt={'session_id':'resumed-real-question'}
    try:
        ensure_acceptance_session(state,attempt,'The actual authorized question')
        ensure_acceptance_session(state,attempt,'The actual authorized question')
        assert db.fetchone('SELECT COUNT(*) AS n FROM sessions')['n']==1
        assert db.fetchone('SELECT COUNT(*) AS n FROM messages')['n']==1
        assert db.fetchone('SELECT content FROM messages')['content']=='The actual authorized question'
    finally:db.close()


@pytest.mark.parametrize('field',['eligible','execution_summary'])
def test_reader_report_refuses_internal_field_names(field):
    from src.analysis.reader_report import validate_report
    text='# 结果\n## 结论\n'+('这是用于验证输出格式的离线合成说明。'*25)+f'\n本轮{field}记录如下。\n## 候选研究空白\n无。\n## 已有工作\n无。\n## 本轮边界\n合成测试。'
    assert any('内部字段' in error for error in validate_report(text,[]))


def test_report_summary_keeps_both_group_conclusions():
    from src.analysis.reader_report import report_summary
    summary=report_summary('# 核查\n## 结论\n实时组：0.43。\n\n精度组：尚未核实。\n## 数值对照\n其他内容。')
    assert '实时组：0.43。' in summary and '精度组：尚未核实。' in summary
    assert '其他内容' not in summary


def test_pdf_table_rows_and_versioned_cache_keep_original_files(tmp_path):
    import fitz
    from src.parsing.pymupdf_parser import cached_table_view
    pdf=tmp_path/'source.pdf'
    with fitz.open() as doc:
        page=doc.new_page(width=420,height=300)
        for x in (30,190,285,385):page.draw_line((x,70),(x,190))
        for y in (70,110,150,190):page.draw_line((30,y),(385,y))
        for index,row in enumerate((('Model','EPE','FPS'),('NewModel','0.40','10'),('TeacherModel','0.10','2'))):
            for x,value in zip((40,200,295),row):page.insert_text((x,95+40*index),value,fontsize=11)
        page.insert_text((30,230),'Table 1: supervised results on ExampleDataset.',fontsize=11)
        doc.save(pdf)
    historical=tmp_path/'full.md';historical.write_text('Historical flattened source',encoding='utf-8')
    original=pdf.read_bytes()
    view=cached_table_view(pdf,tmp_path)
    text=view.read_text(encoding='utf-8')
    assert 'PDF table on page 1' in text
    model_row=next(line for line in text.splitlines() if 'NewModel' in line and '|' in line)
    assert '0.40' in model_row and '0.10' not in model_row
    assert historical.read_text(encoding='utf-8')=='Historical flattened source'
    assert pdf.read_bytes()==original and cached_table_view(pdf,tmp_path)==view


def test_pdf_table_failure_still_preserves_page_text(tmp_path,monkeypatch):
    import fitz
    from src.parsing.pymupdf_parser import extract_markdown_like_text
    pdf=tmp_path/'plain.pdf'
    with fitz.open() as doc:
        page=doc.new_page();page.insert_text((30,60),'Original paragraph survives table recognition failure.')
        doc.save(pdf)
    def fail(*args,**kw):raise RuntimeError('Synthetic table failure')
    monkeypatch.setattr(fitz.Page,'find_tables',fail)
    assert 'Original paragraph survives' in extract_markdown_like_text(pdf)


def test_teacher_and_unknown_date_cannot_be_confirmed_minima():
    from src.analysis.question_evidence import analyze_question
    quote='SyntheticModel has EPE 0.40; TeacherModel has EPE 0.10 on ExampleDataset.'
    profile=bind_profile({'paper_id':'p','evidence':[{'quote':quote}], 'benchmark_results':[
        {'model':'TeacherModel','dataset':'ExampleDataset','metric':'EPE','value':.1,'group':'accuracy','row_role':'teacher','evidence_indices':[1]},
        {'model':'SyntheticModel','dataset':'ExampleDataset','metric':'EPE','value':.4,'group':'accuracy','row_role':'proposed','evidence_indices':[1]}]},quote,'synthetic.md')
    class Model:
        reasoning_model='fixture'
        async def chat_json(self,messages,**kw):
            rows=json.loads(messages[-1]['content'])['rows']
            return {'row_reviews':[{'row_id':r['row_id'],'scope_match':True,'group_verified':True} for r in rows]}
    plan=normalize_plan({'task_type':'benchmark_comparison','comparison':{'groups':['accuracy']}},'最近一年最低EPE',TODAY)
    result=asyncio.run(analyze_question(Model(),{'research_plan':plan},[profile]))
    assert not any(r['eligible'] for r in result['benchmark_table'])
    assert result['benchmark_summary'][0]['status']=='unresolved'


def test_full_pipeline_can_read_then_analyze_then_search_instead_of_fixed_gap_flow(tmp_path,monkeypatch):
    from src.app.factory import build_app
    from src.agents.base import AgentContext
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    monkeypatch.setattr('src.analysis.research_plan.research_today',lambda:TODAY)
    app,config=build_app(tmp_path)
    papers=[]
    for pid,group,value in [('a','精度',.4),('b','实时',.6)]:
        text=f'Synthetic{pid} | ExampleDataset | EPE | {value}\nThis is the proposed {group} model, supervised on ExampleDataset.'
        path=tmp_path/'papers/parsed'/pid/'full.md';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
        papers.append({'id':pid,'title':'Synthetic '+pid,'year':2026,'publication_date':'2026-02-01',
                       'parsed_markdown_path':str(path),'retrieval_status':'parsed','abstract':text,'group':group,'value':value})
    plan={'task_type':'benchmark_comparison','comparison':{'dataset':'ExampleDataset','metric':'EPE','groups':['精度','实时']},'adaptive':True}
    actions=[{'action':'read','paper_ids':['a']},{'action':'analyze'},{'action':'search','queries':['example benchmark']},
             {'action':'read','paper_ids':['b']},{'action':'analyze'},{'action':'deliver'}]
    class Model:
        reasoning_model='fixture';fast_model='fixture'
        async def chat_json(self,messages,**kw):
            system=messages[0]['content']
            if '拆解研究想法' in system:
                return {'research_question':'数值比较','target_task':'ExampleDataset benchmark','search_queries':['example benchmark'],'research_plan':plan}
            if '下一步行动' in system:return actions.pop(0)
            if '按问题读取论文' in system:
                payload=json.loads(messages[-1]['content']);pid=payload['metadata']['id'];p=next(p for p in papers if p['id']==pid)
                return {'evidence':[{'quote':p['abstract']}], 'benchmark_results':[{'model':'Synthetic'+pid,'dataset':'ExampleDataset',
                    'metric':'EPE','value':p['value'],'group':p['group'],'row_role':'proposed','evidence_indices':[1]}]}
            if '原表行' in system:
                rows=json.loads(messages[-1]['content'])['rows']
                return {'row_reviews':[{'row_id':r['row_id'],'scope_match':True,'group_verified':True,'comparison_key':'same protocol'} for r in rows]}
            return {'pass':True,'issues':[]}
        async def chat(self,messages,**kw):
            assert '候选研究空白' not in messages[0]['content']
            return '# Synthetic fixture\n## 结论\n精度 Synthetica：0.4 [1]；实时 Syntheticb：0.6 [2]。\n## 数值对照\n'+('本轮数值由原文表格核实，资料为离线合成样例，不代表真实科学效果。\n'*10)+'## 口径与依据\n相同协议。\n## 本轮边界\n仅测试样例，不宣称真实最新纪录。'
    orch=app.state.orchestrator;orch.llm=Model()
    async def search(*args,**kw):
        args[1]['search_execution']={'completed_queries':[{'query':'example benchmark'}],'new_papers':1}
        return papers
    async def no_gaps(*args):raise AssertionError('Benchmark question entered gap analysis')
    monkeypatch.setattr(orch,'_search',search);monkeypatch.setattr(orch,'_analyze_gaps',no_gaps)
    async def run():
        await orch.kb.upsert_paper(papers[0])
        return await orch.run(AgentContext(workspace_root=tmp_path,workspace_id=app.state.workspace_id,session_id='adaptive',config=config),
                              {'message':'最近一年ExampleDataset最低EPE是多少？','selected_paper_ids':['a']})
    try:
        result=asyncio.run(run())
        assert result.status=='completed',result.error
        assert result.data['task_type']=='benchmark_comparison' and result.data['benchmark_verified_count']==2
        assert '候选研究空白' not in result.data['report_content']
        snapshots=[json.loads(p.read_text(encoding='utf-8')) for p in (tmp_path/'.agent_history/research').glob('adaptive_*.json')]
        assert [d['action'] for d in snapshots[0]['decisions']]==['read','analyze','search','read','analyze','deliver']
        meta=json.loads(app.state.db.fetchone("SELECT metadata_json FROM messages WHERE role='assistant'")['metadata_json'])
        assert meta['task_type']=='benchmark_comparison' and meta['benchmark_verified_count']==2
    finally:
        asyncio.run(app.state.llm.close());app.state.vs.close();app.state.db.close()
