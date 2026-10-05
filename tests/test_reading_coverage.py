"""Offline regressions for reading limits, primary-source tracing and partial delivery."""
import asyncio
import json
from datetime import date
import pytest
from src.analysis.research_plan import normalize_plan, scoped_papers, comparison_row_scope
from src.analysis.provenance import bind_profile


@pytest.mark.parametrize('cap,expected',[(60,60),(12,12),(0,0)])
def test_premature_delivery_reads_selected_papers_up_to_visible_limit(tmp_path,monkeypatch,cap,expected):
    result,snapshot=run_fixture(tmp_path,monkeypatch,cap=cap,count=60)
    assert result.status=='completed',result.error
    assert result.data['innovations_extracted']==expected
    assert result.data['unprocessed_count']==60-expected
    coverage=result.data['reading_coverage']
    assert coverage['read_limit']==cap and coverage['completed']==expected
    assert len(coverage['unread_ids'])==60-expected
    assert coverage['status']==('complete' if expected==60 else 'partial')
    if expected:
        read=next(d for d in snapshot['decisions'] if d['action']=='read')
        assert read['coverage_override']=='deliver' and len(read['paper_ids'])==expected


def test_better_baseline_triggers_local_original_paper_read(tmp_path,monkeypatch):
    result,snapshot=run_fixture(tmp_path,monkeypatch,cap=60,count=1,baseline=True)
    assert result.status=='completed',result.error
    assert result.data['innovations_extracted']==2
    rows=snapshot['analysis']['benchmark_table']
    assert any(r['model']=='BaseNet' and r['value']==.34 and r['eligible'] and r['paper_id']=='original' for r in rows)
    assert not any(r['eligible'] for r in rows if r['row_role']=='baseline')
    reads=[d['paper_ids'] for d in snapshot['decisions'] if d['action']=='read']
    assert reads==[['p0'],['original']]
    assert snapshot['analysis']['benchmark_summary'][0]['best_reported'][0]['value']==.34
    assert snapshot['direction']['search_execution']['baseline_traces'][0]['paper_id']=='original'


def run_fixture(tmp_path,monkeypatch,*,cap,count,baseline=False):
    from src.app.factory import build_app
    from src.agents.base import AgentContext,AgentResult
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    app,config=build_app(tmp_path)
    config['search']['deep_parse_top_k']=cap
    config['research']['max_agent_steps']=6
    config['research']['max_new_papers']=60
    plan={'task_type':'benchmark_comparison','comparison':{'groups':['精度'],'metric':'EPE','dataset':'ExampleDataset'},'adaptive':True}
    class Model:
        calls=0;max_calls=200;reasoning_model='fixture';fast_model='fixture'
        async def chat_json(self,messages,**kw):
            self.calls+=1
            system=messages[0]['content']
            if '拆解研究想法' in system:return {'research_question':'ExampleDataset模型的最低EPE数值比较',
                                              'target_task':'ExampleDataset benchmark','research_plan':plan}
            if '下一步行动' in system:return {'action':'deliver','reason':'Premature fixture delivery'}
            payload=json.loads(messages[-1]['content'])
            assert 'rows' in payload
            return {'row_reviews':[{'row_id':r['row_id'],'scope_match':True,'group_verified':True,
                                    'group':'精度','comparison_key':'same split'} for r in payload['rows']]}
    model=Model();owner=app.state.orchestrator;owner.llm=model
    async def parse(agent,ctx,input_data):
        return AgentResult(status='completed',data={'parsed':[p['id'] for p in input_data['papers']],
                          'metadata_only':[],'failed':[],'deferred':[]})
    async def extract(ctx,parsed,papers,direction=None):
        profiles=[]
        for paper in papers:
            model.calls+=1
            proposed='BaseNet' if paper['id']=='original' else 'Model'+paper['id']
            value=.34 if paper['id']=='original' else .43
            quote=f'{proposed} | ExampleDataset | EPE | {value}'
            rows=[{'model':proposed,'dataset':'ExampleDataset','metric':'EPE','value':value,
                   'group':'精度','row_role':'proposed','evidence_indices':[1]}]
            if baseline and paper['id']=='p0':
                quote+='\nBaseNet | ExampleDataset | EPE | 0.34'
                rows.append({'model':'BaseNet [30]','dataset':'ExampleDataset','metric':'EPE','value':.34,
                             'group':'精度','row_role':'baseline','evidence_indices':[1]})
                # Use the literal model name occurring in this fixture's source.
                quote=quote.replace('BaseNet |','BaseNet [30] |')
            profiles.append(bind_profile({'paper_id':paper['id'],'paper_title':paper['title'],
                'has_fulltext':True,'source_type':'paper_fulltext','publication_date':paper['publication_date'],
                'benchmark_results':rows,'evidence':[{'quote':quote}]},quote,'fixture.md'))
        return profiles
    async def report(*args):return '# 测试报告\n\n## 结论\n仅验证离线流程。'
    monkeypatch.setattr('src.agents.parse_agent.ParseAgent.run',parse)
    monkeypatch.setattr(owner,'_extract_innovations',extract)
    monkeypatch.setattr('src.analysis.reader_report.write_reader_report',report)
    papers=[]
    for index in range(count+(1 if baseline else 0)):
        pid='original' if index==count else f'p{index}'
        path=tmp_path/'papers/parsed'/pid/'full.md';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('Offline local fulltext fixture',encoding='utf-8')
        papers.append({'id':pid,'title':'BaseNet: Original method' if pid=='original' else 'Model'+pid+': New method',
                       'publication_date':'2025-01-17' if pid=='original' else '2026-02-01',
                       'parsed_markdown_path':str(path),'retrieval_status':'parsed'})
    async def execute():
        for paper in papers:await owner.kb.upsert_paper(paper)
        return await owner.run(AgentContext(workspace_root=tmp_path,workspace_id=app.state.workspace_id,
                                           session_id='fixture',config=config),
                                  {'message':'所有ExampleDataset模型最低EPE是多少？','selected_paper_ids':[f'p{i}' for i in range(count)]})
    try:
        result=asyncio.run(execute())
        assert result.status=='completed',result.error
        paths=list((tmp_path/'.agent_history/research').glob('adaptive_*.json'))
        snapshot=json.loads(paths[0].read_text(encoding='utf-8'))
        assert model.calls<=model.max_calls
        return result,snapshot
    finally:
        asyncio.run(app.state.llm.close());app.state.vs.close();app.state.db.close()


def test_recent_search_and_all_model_group_have_separate_time_scopes():
    groups=['精度导向模型 (accuracy-oriented)','实时模型 (real-time)']
    plan=normalize_plan({'task_type':'benchmark_comparison','comparison':{'groups':groups}},
        '最近一年精度模型和实时模型最低EPE分别多少？（精度导向直接搜全部监督模型最低EPE）',date(2026,10,4))
    assert plan['comparison']['all_time_groups']==[groups[0]]
    old={'id':'arxiv:2501.09898','publication_date':'2025-01-17','date_source':'arxiv_first_submission'}
    assert scoped_papers([old],{'research_plan':plan})[0][0]['comparison_context_only']
    assert comparison_row_scope(old,plan,groups[0])[0]=='unrestricted'
    assert comparison_row_scope(old,plan,groups[1])[0]=='outside_scope'
    recent=normalize_plan({'task_type':'benchmark_comparison','comparison':{'groups':groups,'all_time_groups':groups}},
                          '最近一年模型最低EPE',date(2026,10,4))
    assert 'all_time_groups' not in recent['comparison'] and not scoped_papers([old],{'research_plan':recent})[0]


def test_rejected_report_delivers_verified_partial_without_rejected_prose():
    from src.analysis.reader_report import write_reader_report,validate_report
    quote='BaseNet | ExampleDataset | EPE | 0.34'
    profile=bind_profile({'paper_id':'p','paper_title':'BaseNet: Original method','has_fulltext':True,
                          'source_type':'paper_fulltext','evidence':[{'quote':quote}]},quote,'fixture.md')
    papers=[{'id':'p','title':'BaseNet: Original method','url':'https://example.org/p'}]
    analysis={'benchmark_table':[{'paper_id':'p','model':'BaseNet','value':.34,'metric':'EPE','dataset':'ExampleDataset',
                'group':'精度','eligible':True,'source_bound':True,'comparison_key':'same split'}],
              'paper_processing':[{'paper_id':'p','status':'fulltext'}]}
    plan=normalize_plan({'task_type':'benchmark_comparison','comparison':{'groups':['精度']}},'最低EPE是多少？')
    class Model:
        reasoning_model='fixture'
        async def chat(self,*args,**kwargs):return '# 错误初稿\n## 结论\n错误地写成0.45。'
        async def chat_json(self,*args,**kwargs):return {'pass':False,'issues':['数值错误']}
    text=asyncio.run(write_reader_report(Model(),{'research_plan':plan},papers,[profile],analysis))
    assert '0.34' in text and '0.45' not in text and '错误初稿' not in text
    assert '部分' in text and '[1]' in text and 'https://example.org/p' in text
    assert analysis['answer_status']=='partial' and analysis['reader_report_review']['fallback_delivered']
    assert not validate_report(text,[{'number':1}],plan)
