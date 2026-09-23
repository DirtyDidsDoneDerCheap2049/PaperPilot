"""Research-question regressions; synthetic evidence does not measure scientific quality."""
import asyncio
import json

from src.agents.orchestrator import Orchestrator
from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
from src.analysis.direction_coverage import build_direction_coverage
from src.analysis.provenance import bind_profile


QUOTE = 'Cross-scale stereo features interact through epipolar cross attention in the cost volume.'
DIRECTION = {'research_question': '核查沿极线的跨尺度注意力是否已用于代价体构建',
             'method_category': 'attention', 'method_subcategory': 'cross_scale_fusion',
             'claims_to_verify': ['沿极线的跨尺度注意力是否已有实现']}


def profile():
    return bind_profile({'paper_id': 'fixture:fusion', 'has_fulltext': True,
                         'method_category': 'attention', 'method_subcategory': 'cross_scale_fusion',
                         'is_relevant': True, 'evidence': [{'quote': QUOTE, 'section': 'Methods'}]},
                        QUOTE, 'synthetic.txt')


class ResearchModel:
    reasoning_model = 'offline'

    def __init__(self, evidence_id='fixture:fusion#e1'):
        self.evidence_id = evidence_id

    async def chat_json(self, messages, model=None, *, purpose='regular'):
        content = messages[-1]['content']
        if '输出结构：' in content:
            payload = json.JSONDecoder().raw_decode(content)[0]
            return {'paper_assessments': [{'paper_id': p['paper_id'], 'method_relations': ['fusion'],
                    'claim_assessments': [{'claim_id': 'claim_1', 'verdict': 'covered',
                    'reason': '原文明确在代价体中使用沿极线的跨尺度交互', 'evidence_ids': [self.evidence_id]}]}
                    for p in payload['profiles']]}
        return {'matrix': {'cells': []}, 'gaps': [{
            'gap_id': 'g1', 'name': '受遮挡区域约束的跨尺度交互',
            'description': '核查受遮挡区域约束后是否仍存在可重复收益',
            'candidate_status': 'supported_candidate', 'confidence': 0.8,
            'nearest_works': [{'paper_id': 'fixture:fusion', 'difference': '核查区域约束与已知跨尺度融合的差异'}],
            'novelty_basis': '候选差异需要实验验证', 'task_specific_contribution': '减少遮挡区错误对应传播',
            'evidence': [{'paper_id': 'fixture:fusion', 'evidence_ids': [self.evidence_id]}],
            'decisive_experiment': {'nearest_method_baselines': ['fixture:fusion'],
                                   'minimal_change': '仅加入遮挡区域约束',
                                   'supporting_metrics': ['遮挡区EPE'], 'stop_conditions': ['匹配参数量后收益消失']},
        }]}


def test_non_distillation_research_keeps_evidence_and_its_own_experiment(tmp_path):
    p = profile()
    analysis = asyncio.run(EvidenceGroundedAnalyzer(ResearchModel()).analyze(DIRECTION, [p]))
    assert analysis['analysis_scope'] == 'research_direction'
    assert analysis['matrix']['cells'][0]['paper_ids'] == ['fixture:fusion']
    assert analysis['matrix']['cells'][0]['evidence_ids'] == ['fixture:fusion#e1']
    orch = Orchestrator(None, None, None, None)
    analysis = orch._enforce_analysis_reliability(DIRECTION, [p], analysis)
    assert analysis['reliability']['matrix_valid']
    assert len(analysis['gaps']) == 1
    gap = analysis['gaps'][0]
    assert gap['decisive_experiment']['minimal_change'] == '仅加入遮挡区域约束'
    assert gap['candidate_status'] == 'narrow_candidate'
    assert gap['confidence'] <= 0.65
    from types import SimpleNamespace
    report = asyncio.run(orch._generate_report(SimpleNamespace(workspace_root=tmp_path), DIRECTION,
                        [{'id': 'fixture:fusion', 'title': 'Synthetic fusion'}], [p], analysis, []))
    content = report.read_text(encoding='utf-8')
    assert DIRECTION['research_question'] in content
    assert '当前方向的主张覆盖记录' in content and 'fixture:fusion#e1' in content
    assert '仅加入遮挡区域约束' in content
    assert '| 蒸馏 | T/S/I/L |' not in content
    # Even a source-grounded candidate needs a separate critic verdict.
    reviewed = orch._apply_critic_verdicts(analysis, {}, [p])
    assert not reviewed['gaps'] and reviewed['provisional_gaps']


def test_fabricated_evidence_cannot_create_coverage_or_a_candidate():
    analysis = asyncio.run(EvidenceGroundedAnalyzer(ResearchModel('invented#e1')).analyze(DIRECTION, [profile()]))
    assert analysis['matrix']['cells'] == []
    assert not analysis['gaps']
    assert analysis['provisional_gaps'][0]['candidate_status'] == 'insufficient_evidence'


def test_abstract_unknown_claim_and_fallback_cannot_create_direct_coverage():
    claims = [{'claim_id': 'claim_1', 'claim': 'test'}]
    audit = {'paper_id': 'p', 'assessment_status': 'audited', 'source_type': 'paper_fulltext',
             'method_relations': ['fusion'], 'evidence': [{'evidence_id': 'p#e1', 'evidence_level': 'direct_fulltext'}],
             'claim_assessments': [{'claim_id': 'claim_1', 'verdict': 'covered', 'evidence_ids': ['p#e1']}]}
    for changes in ({'source_type': 'paper_abstract'}, {'audit_mode': 'deterministic_fallback'},
                    {'claim_assessments': [{'claim_id': 'invented', 'verdict': 'covered', 'evidence_ids': ['p#e1']}]},
                    {'assessment_status': 'unassessed'}):
        matrix, records = build_direction_coverage(DIRECTION, claims, [{**audit, **changes}])
        assert matrix['cells'] == [] and records == []




def test_generic_question_fields_and_followup_survive_parsing():
    previous = {'research_question': '自适应索引是否改善混合数据库负载？',
                'research_domain': 'databases', 'target_task': 'adaptive indexing',
                'search_queries': ['adaptive indexing mixed workload']}
    class Model:
        async def chat_json(self, messages, **kwargs):
            return previous
    orch = Orchestrator(None, Model(), None, None)
    parsed = asyncio.run(orch._parse_direction(previous['research_question']))
    assert orch._direction_is_usable(parsed)
    assert parsed['target_task'] == previous['target_task']
    assert parsed['method_component'] == ''
    class Empty:
        async def chat_json(self, *args, **kwargs):
            return {}
    orch.llm = Empty()
    restored = asyncio.run(orch._parse_direction('继续核查上次的问题', parsed))
    assert restored['research_domain'] == 'databases'
    assert restored['search_queries'] == previous['search_queries']
    invalid = asyncio.run(orch._parse_direction('重新生成报告'))
    assert not orch._direction_is_usable(invalid)




def test_text_distillation_uses_generic_audit_and_keeps_domain_fields():
    from src.parsing.innovation_extractor import InnovationExtractor
    direction = {'research_question': '核查文本分类中的蒸馏实现',
                 'target_task': 'text classification', 'method_category': 'knowledge_distillation'}
    quote = 'We train a compact text classifier by matching the teacher probability distribution.'
    raw = {'research_domain': 'natural language processing', 'task_or_problem': 'text classification',
           'method_summary': 'match teacher probabilities', 'limitations': ['one language'],
           'evidence': [{'quote': quote}]}
    p = InnovationExtractor._validate(raw, 'text-fixture')
    p['has_fulltext'] = True
    p = bind_profile(p, quote, 'synthetic.txt')
    class Model:
        async def chat_json(self, messages, **kwargs):
            content = messages[-1]['content']
            if '输出结构：' in content:
                payload = json.JSONDecoder().raw_decode(content)[0]
                assert payload['profiles'][0]['task_or_problem'] == 'text classification'
                assert payload['profiles'][0]['limitations'] == ['one language']
                assert 'distills_epipolar' not in content
                return {'paper_assessments': [{'paper_id': 'text-fixture', 'method_relations': ['other'],
                        'claim_assessments': [{'claim_id': 'claim_1', 'verdict': 'covered',
                                              'evidence_ids': ['text-fixture#e1']}]}]}
            return {'gaps': []}
    result = asyncio.run(EvidenceGroundedAnalyzer(Model()).analyze(direction, [p]))
    assert result['analysis_scope'] == 'research_direction'
    assert result['matrix']['design'] == 'direction_claim_evidence'
    assert result['matrix']['cells'][0]['paper_ids'] == ['text-fixture']
    assert 'coverage_record' not in result['coverage_audit'][0]


def test_followup_recovers_candidates_after_the_report_prefix(tmp_path):
    from src.knowledge.sqlite_store import SQLiteStore
    db = SQLiteStore(tmp_path / 'history.db')
    db.init_schema()
    try:
        # Real schema and stored JSON; no dependence on a formatted report prefix.
        db.execute('INSERT INTO workspaces (id, name, root_path) VALUES (?,?,?)', ('w', 'fixture', str(tmp_path)))
        db.execute('INSERT INTO sessions (id,workspace_id,title) VALUES (?,?,?)', ('s', 'w', 'direction'))
        candidate = {'gap_id': 'g2', 'name': '区域退出条件', 'description': '核查提前退出与遮挡误差',
                     'candidate_status': 'insufficient_evidence', 'required_evidence': ['补充退出条件的最近工作']}
        db.execute('INSERT INTO gap_analyses (id,session_id,direction,gaps_json,search_log_json) VALUES (?,?,?,?,?)',
                   ('a', 's', json.dumps(DIRECTION), '[]', json.dumps({'provisional_gaps': [candidate]})))
        orch = Orchestrator(db, None, None, None)
        context = orch._latest_research_context('s')
        assert '区域退出条件' in context and '补充退出条件的最近工作' in context
        assert 'insufficient_evidence' in context and '不是已证实事实' in context
        class Model:
            async def chat_json(self, messages, **kwargs):
                assert '区域退出条件' in messages[-1]['content']
                return {}
        orch.llm = Model()
        restored = asyncio.run(orch._parse_direction('继续核查上次的候选', DIRECTION, context))
        assert restored['research_question'] == DIRECTION['research_question']
        assert restored['claims_to_verify'] == DIRECTION['claims_to_verify']
    finally:
        db.close()


def test_fifty_existing_papers_do_not_skip_other_research_queries(monkeypatch):
    class KB:
        async def search_related(self, *args):
            return []
    calls = []
    async def search(query, *args, **kwargs):
        calls.append(query)
        return []
    async def no_wait(*args):
        pass
    class Model:
        async def chat_json(self, *args, **kwargs):
            return {}
    orch = Orchestrator(None, Model(), None, KB())
    papers = [{'id': f'p{i}', 'title': f'Synthetic existing work {i}'} for i in range(50)]
    monkeypatch.setattr(orch, '_selected_papers', lambda ids: papers)
    monkeypatch.setattr(orch, '_hydrate_paper_from_db', lambda paper: paper)
    monkeypatch.setattr('src.tools.search_tools.search_all', search)
    monkeypatch.setattr('src.agents.orchestrator.asyncio.sleep', no_wait)
    direction = {**DIRECTION, 'search_queries': ['stereo fusion', 'epipolar attention', 'cost volume cross scale', 'additional query']}
    asyncio.run(orch._search('研究这个方向', direction, {'search': {'max_rounds': 3}}, [p['id'] for p in papers]))
    assert calls == direction['search_queries'][:3]
    assert direction['search_execution']['unexecuted_queries'] == ['additional query']
    assert direction['search_execution']['exhaustive'] is False
