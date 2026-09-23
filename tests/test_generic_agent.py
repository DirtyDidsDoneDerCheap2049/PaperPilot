"""Domain-independent workflow contracts; synthetic material is not a quality benchmark."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.orchestrator import Orchestrator
from src.analysis.direction_coverage import build_direction_coverage, enforce_candidate_consistency
from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer as Analyzer
from src.analysis.provenance import bind_profile
from src.parsing.innovation_extractor import InnovationExtractor
from src.parsing.schemas import DirectionParseResult


def compact(quote, source=None, **extra):
    return Analyzer.compact_profile(bind_profile({
        'paper_id': 'p', 'has_fulltext': True, 'evidence': [{'quote': quote}], **extra,
    }, source if source is not None else quote, 'fixture.txt'))


@pytest.mark.parametrize('domain,task,method', [
    ('computer vision', 'image segmentation', 'uncertainty calibration'),
    ('language models', 'long context generation', 'adaptive cache eviction'),
    ('generative modeling', 'image generation', 'diffusion sampling'),
    ('graph learning', 'node classification', 'heterophily message passing'),
    ('reinforcement learning', 'offline policy learning', 'conservative value learning'),
    ('recommendation', 'sequential recommendation', 'contrastive representation learning'),
    ('computer vision', 'stereo matching', 'feature distillation'),
    ('databases', 'mixed workload indexing', 'adaptive index selection'),
    ('materials science', 'battery cycle life', 'electrolyte additive screening'),
])
def test_same_pipeline_preserves_each_deep_learning_problem(tmp_path, domain, task, method):
    quote = f'We evaluate {method} for {task} under controlled training conditions.'
    direction = {'research_domain': domain, 'target_task': task, 'method_component': method,
                 'research_question': f'Which implementations of {method} exist for {task}?',
                 'claims_to_verify': [f'{method} for {task}'], 'search_queries': [f'{method} {task}']}
    class Model:
        async def chat_json(self, messages, **kwargs):
            system, content = messages[0]['content'], messages[-1]['content']
            assert 'stereo_matching_stage' not in system and 'mono_to_stereo' not in content
            if '拆解研究想法' in system:
                return direction
            if '实现层面的创新点' in system:
                return {'research_domain': domain, 'task_or_problem': task, 'method_component': method,
                        'innovation_detail': method, 'method_subcategory': method, 'evidence': [{'quote': quote}]}
            if '输出结构：' in content:
                payload = json.JSONDecoder().raw_decode(content)[0]
                assert payload['profiles'][0]['task_or_problem'] == task
                assert 'distills_epipolar' not in content
                return {'paper_assessments': [{'paper_id': 'p', 'method_relations': ['other'],
                        'method_facts': {'task': {'value': task, 'evidence_ids': ['p#e1']}},
                        'claim_assessments': [{'claim_id': 'claim_1', 'verdict': 'covered',
                                              'reason': 'Synthetic method evidence', 'evidence_ids': ['p#e1']}]}]}
            return {'gaps': [], 'coverage_summary': 'Synthetic fixture only.'}
    model=Model();orch=Orchestrator(None,model,None,None)
    parsed=asyncio.run(orch._parse_direction(direction['research_question']))
    assert parsed['target_task']==task and parsed['method_component']==method
    path=tmp_path/'paper.md';path.write_text(quote,encoding='utf-8')
    profile=asyncio.run(InnovationExtractor(model).extract_from_markdown('p',{'title':'Synthetic method'},path))
    profile['has_fulltext']=True;profile=bind_profile(profile,quote,'paper.md')
    result=asyncio.run(Analyzer(model).analyze(parsed,[profile]))
    assert result['analysis_scope']=='research_direction'
    assert result['matrix']['design']=='direction_claim_evidence'
    assert result['matrix']['cells'][0]['paper_ids']==['p']
    assert result['coverage_records'][0]['method_facts']['task']['value']==task
    result=orch._enforce_analysis_reliability(parsed,[profile],result)
    report=asyncio.run(orch._generate_report(SimpleNamespace(workspace_root=tmp_path),parsed,
                       [{'id':'p','title':'Synthetic method'}],[profile],result,[]))
    text=report.read_text(encoding='utf-8')
    assert task in text and 'p#e1' in text
    assert '立体任务特定贡献' not in text and 'T/S/I/L' not in text


def kd_assessment():
    return {'paper_id':'p','method_relations':['distillation'],
            'method_facts':{key:{'value':key,'evidence_ids':['p#e1']} for key in
                            ('teacher_signal','student','imitation_target','matching_loss')},
            'claim_assessments':[{'claim_id':'claim_1','verdict':'covered','evidence_ids':['p#e1']}]}


KD='The teacher provides feature distributions to the student, trained with an explicit MSE matching loss.'
FUSION='We fuse two feature streams through cross attention during inference.'


@pytest.mark.parametrize('quote,source,supports,valid', [
    (KD,KD,'',True), (KD,FUSION,'',False), (FUSION,FUSION,KD,False),
    ('We do not use teacher or student feature distributions or a matching loss.',
     'We do not use teacher or student feature distributions or a matching loss.','',False),
])
def test_distillation_requires_actual_positive_method_evidence(quote,source,supports,valid):
    p=compact(quote,source)
    p['evidence'][0]['supports']=supports
    audit=Analyzer(None)._normalize_batch({'paper_assessments':[kd_assessment()]},[p])[0]
    assert ('distillation' in audit['method_relations']) is valid
    assert (audit['claim_assessments'][0]['verdict']=='covered') is valid


@pytest.mark.parametrize('missing', ['teacher_signal','student','imitation_target','matching_loss'])
def test_each_missing_distillation_fact_blocks_coverage(missing):
    item=kd_assessment();del item['method_facts'][missing]
    audit=Analyzer(None)._normalize_batch({'paper_assessments':[item]},[compact(KD)])[0]
    assert audit['claim_assessments'][0]['verdict']=='insufficient_evidence'
    assert 'distillation' not in audit['method_relations']


@pytest.mark.parametrize('kind', ['prior_knowledge','survey_or_repository','paper_abstract','model_generated_summary'])
def test_background_and_abstract_never_create_direct_coverage(kind):
    p=compact('A reference text describes representation learning.',source_type=kind)
    item={'paper_id':'p','method_relations':['representation_learning'],
          'claim_assessments':[{'claim_id':'c','verdict':'covered','evidence_ids':['p#e1']}]}
    audit=Analyzer(None)._normalize_batch({'paper_assessments':[item]},[p])
    matrix,records=build_direction_coverage({},[{'claim_id':'c','claim':'method'}],audit)
    assert not matrix['cells'] and not records


def test_transfer_endpoints_remain_explicit_and_are_not_defaulted():
    quote='We transfer representations from image classification to speech recognition.'
    facts={k:{'value':v,'evidence_ids':['p#e1']} for k,v in
           [('source_task','image classification'),('target_task','speech recognition')]}
    audit=Analyzer(None)._normalize_batch({'paper_assessments':[{'paper_id':'p','method_facts':facts}]},[compact(quote)])[0]
    assert audit['method_facts']==facts
    facts['source_task']['evidence_ids']=['invented']
    audit=Analyzer(None)._normalize_batch({'paper_assessments':[{'paper_id':'p','method_facts':facts}]},[compact(quote)])[0]
    assert 'source_task' not in audit['method_facts']


def test_model_outage_cannot_create_coverage_or_candidates():
    class Offline:
        async def chat_json(self,*args,**kwargs):raise ConnectionError('offline fixture')
    p=bind_profile({'paper_id':'p','has_fulltext':True,'evidence':[{'quote':KD}]},KD,'fixture.txt')
    result=asyncio.run(Analyzer(Offline()).analyze({'claims_to_verify':['Verify method']},[p]))
    assert not result['gaps'] and not result['coverage_records'] and not result['matrix']['cells']
    assert result['coverage_audit'][0]['audit_mode']=='deterministic_fallback'


def test_old_personal_relevance_flag_does_not_hide_evidence():
    class Model:
        async def chat_json(self,messages,**kwargs):
            if '输出结构：' in messages[-1]['content']:
                return {'paper_assessments':[{'paper_id':'p','method_relations':['fusion'],
                        'claim_assessments':[{'claim_id':'claim_1','verdict':'covered','evidence_ids':['p#e1']}]}]}
            return {'gaps':[]}
    profile=bind_profile({'paper_id':'p','has_fulltext':True,'is_relevant':False,
                          'evidence':[{'quote':FUSION}]},FUSION,'fixture.txt')
    result=asyncio.run(Analyzer(Model()).analyze({'claims_to_verify':['fusion mechanism']},[profile]))
    assert result['matrix']['cells'][0]['paper_ids']==['p']


def test_legacy_component_is_read_compatible_but_not_emitted():
    value=DirectionParseResult(stereo_matching_stage='feature_extraction').model_dump()
    assert value['method_component']=='feature_extraction' and 'stereo_matching_stage' not in value
    profile=InnovationExtractor._validate({'method_component':'new arbitrary component'},'p')
    assert profile['method_component']=='new arbitrary component'
    assert 'stereo_matching_stage' not in profile


def test_old_off_topic_paper_is_reconsidered_for_a_new_question(monkeypatch):
    paper={'id':'p','title':'Speech representation learning','retrieval_status':'off_topic'}
    class KB:
        async def search_related(self,*args):return [{'paper':paper}]
    async def online(*args,**kwargs):return []
    monkeypatch.setattr('src.tools.search_tools.search_all',online)
    orch=Orchestrator(None,None,None,KB())
    monkeypatch.setattr(orch,'_selected_papers',lambda ids:[])
    monkeypatch.setattr(orch,'_hydrate_paper_from_db',lambda p:p)
    result=asyncio.run(orch._search('Speech research',{'search_queries':['speech representation learning']}))
    assert result[0]['id']=='p'


def test_candidate_statuses_are_exclusive_and_rejection_is_not_counterevidence():
    result=enforce_candidate_consistency({'gaps':[{'gap_id':'a'}],
        'provisional_gaps':[{'gap_id':'a'},{'gap_id':'b','candidate_status':'contradicted','contradicted_by':['invented']}],
        'rejected_gaps':[{'gap_id':'c','candidate_status':'contradicted','contradicted_by':['invented']}]})
    assert [x['gap_id'] for x in result['provisional_gaps']]==['b']
    assert result['rejected_gaps'][0]['candidate_status']=='rejected'
    assert not result['rejected_gaps'][0]['contradicted_by']


def test_runtime_contains_no_domain_rules_except_legacy_read_aliases():
    root=Path(__file__).resolve().parents[1]
    for path in (root/'src').rglob('*.py'):
        text=path.read_text(encoding='utf-8').replace('stereo_matching_stage','')
        assert 'stereo' not in text.lower(),path
    assert not (root/'src/knowledge/prior_knowledge.py').exists()
    assert not (root/'src/knowledge/awesome_deep_stereo.py').exists()


def test_offline_remap_rechecks_quotes_and_preserves_history(tmp_path, monkeypatch):
    from src.knowledge.sqlite_store import SQLiteStore
    from scripts.rewrite_latest_report import main, saved_material
    import sys
    workspace=tmp_path/'workspace';(workspace/'db').mkdir(parents=True)
    (workspace/'reports').mkdir();(workspace/'papers/parsed').mkdir(parents=True)
    source=workspace/'papers/parsed/p.md';source.write_text(FUSION,encoding='utf-8')
    old_report=workspace/'reports/original.md';old_report.write_text('Historical result: keep exactly.',encoding='utf-8')
    db=SQLiteStore(workspace/'db/ai_reader.db');db.init_schema()
    db.execute('INSERT INTO workspaces (id,name,root_path) VALUES (?,?,?)',('w','fixture',str(workspace)))
    db.execute('INSERT INTO sessions (id,workspace_id,title) VALUES (?,?,?)',('s','w','fixture'))
    db.execute('INSERT INTO papers (id,title,parsed_markdown_path) VALUES (?,?,?)',('p','Synthetic method','papers/parsed/p.md'))
    db.execute('INSERT INTO innovation_profiles (paper_id,profile_json) VALUES (?,?)',
               ('p',json.dumps({'has_fulltext':True,'evidence_verified':True,'evidence':[{'quote':KD,'source_verified':True}]})))
    db.execute('INSERT INTO gap_analyses (id,session_id,direction,search_log_json,gaps_json,report_path) VALUES (?,?,?,?,?,?)',
               ('a','s','{"target_task":"feature fusion"}','{"papers":[{"id":"p"}]}','[]',str(old_report)))
    row=db.fetchone('SELECT * FROM gap_analyses WHERE id=?',('a',))
    _,_,profiles,_=saved_material(db,workspace,row)
    assert not profiles[0]['evidence'][0]['source_verified']
    db.close()
    monkeypatch.setenv('READER_STORAGE','sqlite')
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.setattr(sys,'argv',['rewrite_latest_report.py','--workspace',str(workspace),'--remap-only'])
    asyncio.run(main());asyncio.run(main())
    db=SQLiteStore(workspace/'db/ai_reader.db')
    try:
        assert db.fetchone('SELECT * FROM gap_analyses WHERE id=?',('a',))==row
        assert len(db.fetchall('SELECT id FROM gap_analyses'))==1
        assert old_report.read_text(encoding='utf-8')=='Historical result: keep exactly.'
        assert len(list((workspace/'reports').glob('*.md')))==3
    finally:db.close()
