import asyncio
import json
from types import SimpleNamespace as NS
from src.analysis.reader_report import validate_report, write_reader_report

def test_failed_report_review_keeps_draft_without_publishing():
    import pytest
    class Model:
        reasoning_model='fake'
        async def chat(self,*args,**kwargs):return '草稿正文'
        async def chat_json(self,*args,**kwargs):raise RuntimeError('余额不足')
    analysis={}
    with pytest.raises(RuntimeError,match='余额不足'):
        asyncio.run(write_reader_report(Model(),{},[],[],analysis))
    assert analysis['reader_report_draft']=='草稿正文'
    assert analysis['reader_report_review']['passed'] is False
    assert 'reader_report' not in analysis

def test_rejected_collection_cannot_keep_provisional_status():
    from src.agents.orchestrator import Orchestrator
    orch=Orchestrator(None,None,None,None)
    analysis=orch._normalize_analysis({'rejected_gaps':[{'name':'covered method','candidate_status':'insufficient_evidence'}]})
    assert analysis['rejected_gaps'][0]['candidate_status']=='rejected'
    analysis=orch._apply_critic_verdicts(analysis,{'gap_reviews':[]},[])
    assert analysis['rejected_gaps'][0]['candidate_status']=='rejected'
    assert not analysis['rejected_gaps'][0]['contradicted_by']

def test_access_page_and_empty_abstract_never_generate_method_evidence(tmp_path):
    from src.parsing.content_quality import unusable_fulltext_reason
    from src.parsing.innovation_extractor import InnovationExtractor
    class NoModel:
        async def chat_json(self,*args,**kwargs):raise AssertionError('Should not send empty or blocked text')
    path=tmp_path/'blocked.md';path.write_text('## Page 1\nJavaScript is disabled in your browser. Please enable JavaScript to proceed.')
    extractor=InnovationExtractor(NoModel())
    assert asyncio.run(extractor.extract_from_markdown('p',{},path))['error']
    assert asyncio.run(extractor.extract_from_abstract('p',{'title':'A plausible title'}))['error']
    assert not unusable_fulltext_reason('## Page 1\nA research article about access control and browser security.')

def test_followup_exposes_unread_papers_and_prioritizes_requested_counterexamples():
    from src.analysis.research_completion import pending_material,select_retry_papers
    papers=[{'id':f'p{i}','title':f'paper {i}','abstract':'abstract'} for i in range(11)]
    profiles=[{'paper_id':p['id']} for p in papers[:9]]
    analysis={'coverage_audit':[{'paper_id':p['id'],'assessment_status':'audited'} for p in papers[:9]]}
    unread=pending_material(papers,profiles,analysis)
    assert [p['id'] for p in unread]==['p9','p10']
    selected=select_retry_papers(papers,[p['id'] for p in papers],set(),{'p9','p10'},set())
    assert [p['id'] for p in selected[:2]]==['p9','p10']
    assert len(selected)==8
    assert not select_retry_papers(papers,['p9'],set(),{'p9'},{'p9'})

def test_delivery_summary_is_the_authored_conclusion_not_audit_or_other_sections():
    from src.analysis.reader_report import report_summary
    text='# 研究\n## 结论\n这是有依据但仍有限制的判断。[1]\n\n第二段说明。\n## 已有工作\n其他内容'
    assert report_summary(text)=='这是有依据但仍有限制的判断。[1]'
    assert report_summary('# 历史报告\n旧格式原文')==''

def test_compact_evidence_keeps_appended_verified_method_and_original_id():
    from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
    quote='A complete method paragraph. '*25+'The matching loss is MSE.'
    profile={'paper_id':'p','has_fulltext':True,'evidence_verified':True,'document_sha256':'hash',
             'evidence':[{'quote':'Unverified model paraphrase'} for _ in range(12)]+[
                 {'quote':quote,'source_verified':True,'source_span':{'sha256':'hash'}}]}
    evidence=EvidenceGroundedAnalyzer.compact_profile(profile)['evidence']
    assert evidence[0]['evidence_id']=='p#e13'
    assert evidence[0]['quote']==quote
    assert evidence[0]['evidence_level']=='direct_fulltext'
    assert len(evidence)==10

def test_visible_parse_budget_is_not_overridden_by_legacy_hidden_cap():
    from src.agents.orchestrator import Orchestrator
    assert Orchestrator(None,None,None,None)._parse_budget([{}]*80,60,{'search':{'deep_parse_max_k':10}})==60

def test_new_question_cannot_inherit_invented_novelty_assertions():
    from src.agents.orchestrator import Orchestrator
    class Model:
        async def chat_json(self,messages):
            assert 'old assistant claim' not in messages[-1]['content']
            return {'research_question':'查找检索算法的空白','target_task':'retrieval','search_queries':['retrieval evidence'],
                    'claims_to_verify':['世界上不存在相关方法']}
    direction=asyncio.run(Orchestrator(None,Model(),None,None)._parse_direction('检索算法有哪些空白？',recent_context='old assistant claim'))
    assert direction['claims_to_verify']==[]

def test_report_rejects_unknown_citations_and_internal_dump():
    text='# 结果\n## 结论\n'+('有依据的研究说明。'*50)+'[99]\n## 候选研究空白\n## 已有工作\n## 本轮边界\ncoverage_summary'
    assert len(validate_report(text,[{'number':1}]))==2

def test_writer_repairs_review_failure_without_upgrading_provisional():
    class Model:
        reasoning_model='fake'
        def __init__(self):self.drafts=0;self.reviews=0
        async def chat(self,messages,**kwargs):
            self.drafts+=1
            if self.drafts==2:assert '候选仍待核实' in messages[-1]['content']
            return '# 研究结论\n## 结论\n'+('现有证据支持方法比较，候选仍待核实。'*20)+'[1]\n## 候选研究空白\n### 1. 候选（待核实）\n**空白点：**尚缺受控比较。\n\n**与已有工作的差异：**现有摘要未提供同条件比较。\n## 已有工作\n已取得论文摘要。\n## 本轮边界\n尚缺全文。'
        async def chat_json(self,messages,**kwargs):
            self.reviews+=1
            payload=json.loads(messages[-1]['content'])
            assert payload['input']['candidates']['provisional_gaps'][0]['name']=='候选'
            return {'pass':self.reviews==2,'issues':['候选仍待核实']}
    model=Model();analysis={'provisional_gaps':[{'name':'候选'}]}
    from src.analysis.provenance import bind_profile
    profile=bind_profile({'paper_id':'p','evidence':[{'quote':'An abstract sentence'}]},'An abstract sentence','abstract')
    text=asyncio.run(write_reader_report(model,{'research_question':'问题'},[{'id':'p','title':'论文'}],[profile],analysis))
    assert model.drafts==2 and analysis['reader_report_review']['passed']
    assert '## 参考文献' in text and '（摘要）' in text


def test_new_report_requires_explicit_gaps_before_related_work():
    conclusion='本轮资料支持进一步比较不同训练条件下的方法表现。'*15
    text='# 方向解析\n## 结论\n'+conclusion+'\n## 候选研究空白\n本轮未识别出有依据的候选空白。\n## 已有工作\n已覆盖主要机制。\n## 本轮边界\n资料有限。'
    assert validate_report(text,[])==[]
    assert '缺少## 候选研究空白' in validate_report(text.replace('## 候选研究空白','## 研究判断'),[])
    reversed_order=text.replace('## 候选研究空白','## TEMP').replace('## 已有工作','## 候选研究空白').replace('## TEMP','## 已有工作')
    assert any('章节顺序' in error for error in validate_report(reversed_order,[]))

def test_report_sources_exclude_unverified_and_background_material():
    from src.analysis.reader_report import report_sources
    from src.analysis.provenance import bind_profile
    verified=bind_profile({'paper_id':'good','evidence':[{'quote':'An actual abstract sentence'}]},'An actual abstract sentence','abstract')
    papers=[{'id':pid,'title':pid} for pid in ('good','bare','prior')]
    profiles=[verified,{'paper_id':'bare','evidence':[{'quote':'Invented quote'}]},
              dict(verified,paper_id='prior',source_type='prior_knowledge')]
    assert [s['paper_id'] for s in report_sources(papers,profiles)]==['good']


def test_report_counts_completed_followup_separately_from_unadmitted_search_results():
    from src.analysis.reader_report import report_execution_summary
    direction={'search_execution':{'completed_queries':[{'query':'q1'},{'query':'q2'},{'query':'q1'}],
        'unexecuted_queries':['q1','q3'],'new_papers':1,'deferred_new_records':29}}
    analysis={'paper_processing':[{'paper_id':'p','status':'fulltext'}],
              'followup_actions':[{'deferred':29}]}
    result=report_execution_summary(direction,[{'id':'p'},{'id':'p'},{'id':'a'}],analysis)
    assert result['included_records']==2
    assert result['included_processing']=={'fulltext':1,'not_analyzed':1}
    assert result['executed_queries']==2 and result['unexecuted_queries']==['q3']
    assert result['search_records_not_admitted']==29

def test_followup_executes_pending_query_without_inventing_success(monkeypatch):
    from src.analysis.research_completion import complete_research
    calls=[]
    async def search(query,*args,**kwargs):
        calls.append(query)
        class Results(list):diagnostics=[{'source':'arxiv','status':'failed','http_status':429}]
        return Results()
    monkeypatch.setattr('src.tools.search_tools.search_all',search)
    class Owner:
        async def _create_agent_run(self,*args):return 'run'
        async def _progress(self,*args):pass
        async def _finish_agent_run(self,*args):pass
        async def _analyze_gaps(self,direction,profiles):return {'gaps':[]}
        def _normalize_analysis(self,a):return a
        def _enforce_analysis_reliability(self,d,p,a):return a
    async def plan(*args):return {'queries':['targeted query'],'retry_paper_ids':[]}
    owner=Owner();owner.llm=NS(chat_json=plan)
    direction={'research_question':'question','search_queries':['pending query'],'search_execution':{'completed_queries':[]}}
    result=asyncio.run(complete_research(owner,NS(config={},session_id='s'),direction,[],[],{'provisional_gaps':[{'name':'candidate'}]},{}))
    assert calls==['targeted query','pending query']
    assert result[2]['followup_actions'][0]['sources'][0]['status']=='failed'
    assert len(result[2]['followup_actions'])==3
