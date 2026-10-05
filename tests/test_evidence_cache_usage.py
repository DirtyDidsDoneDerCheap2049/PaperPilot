"""Offline request-reuse and provider billing regressions; no external model calls."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from src.analysis.evidence_cache import load_profile, path_for, request_key, save_profile
from src.analysis.provenance import bind_profile
from src.analysis.question_evidence import extraction_messages, extract_question_profiles
from src.agents.base import AgentContext, AgentResult
from src.llm.deepseek_client import DeepSeekClient, LLMOutputError
from src.llm.usage import normalize_usage, summarize_usage


SOURCE = 'The original paper provides the exact experimental evidence.'


def extraction_fixture(workspace):
    class Model:
        fast_model = 'fixture-model'
        _base_url = 'https://fixture.example/v1'
        effort = 'max'
        calls = 0
        max_calls = 200
        local_evidence_cache_hits = 0

        def request_options(self, model, structured=False):
            return {'model': model, 'reasoning_effort': self.effort}

        async def chat_json(self, messages):
            self.calls += 1
            data = json.loads(messages[-1]['content'])
            return {'method_summary': data['research_plan']['objective'],
                    'evidence': [{'section': 'Abstract', 'quote': data['content'], 'supports': 'Fixture'}]}

    latest, events = {}, []

    def verified(pid, text):
        cached = latest.get(pid)
        return cached if cached and cached['document_sha256'] == hashlib.sha256(text.encode()).hexdigest() else None

    async def progress(*args):
        events.append(args)

    owner = NS(llm=Model(), db=NS(fetchone=lambda *a: {}), _verified_cached_profile=verified, _progress=progress)
    paper = {'id': 'paper', 'title': 'Fixture paper', 'abstract': SOURCE}
    ctx = AgentContext(workspace_root=workspace, config={})
    parsed = AgentResult(status='completed', data={'metadata_only': ['paper']})

    def read(question, **changes):
        plan = {'task_type': 'literature_review', 'objective': question, 'extraction_fields': ['method'], **changes}
        profiles = asyncio.run(extract_question_profiles(owner, ctx, parsed, [paper], {'research_plan': plan}))
        latest['paper'] = profiles[0]
        return profiles[0]

    return owner, paper, latest, events, read


def test_question_a_b_a_reuses_distinct_verified_results(tmp_path):
    owner, _, _, _, read = extraction_fixture(tmp_path)
    first = read('Question A')
    assert read('Question B')['method_summary'] == 'Question B'
    assert read('Question A')['method_summary'] == first['method_summary']
    assert owner.llm.calls == 2 and owner.llm.local_evidence_cache_hits == 1
    assert len(list((tmp_path/'.agent_history/cache/question_evidence').glob('*.json'))) == 2


def test_report_layout_and_adaptive_flag_do_not_reread_paper(tmp_path):
    owner, _, _, _, read = extraction_fixture(tmp_path)
    read('Same question', report_sections=['Summary'], adaptive=True)
    read('Same question', report_sections=['Answer', 'Sources'], adaptive=False)
    assert owner.llm.calls == 1 and owner.llm.local_evidence_cache_hits == 1


@pytest.mark.parametrize('change', ['text', 'endpoint', 'model', 'effort', 'criteria', 'fields'])
def test_changed_evidence_request_does_not_reuse_stale_latest_row(tmp_path, change):
    owner, paper, _, _, read = extraction_fixture(tmp_path)
    first = read('Same question')
    kwargs = {}
    if change == 'text': paper['abstract'] += ' New experiment.'
    if change == 'endpoint': owner.llm._base_url = 'https://another.example/v1'
    if change == 'model': owner.llm.fast_model = 'another-model'
    if change == 'effort': owner.llm.effort = 'high'
    if change == 'criteria': kwargs['success_criteria'] = ['Check speed']
    if change == 'fields': kwargs['extraction_fields'] = ['runtime']
    second = read('Same question', **kwargs)
    assert owner.llm.calls == 2
    assert second['extraction_request_key'] != first['extraction_request_key']


def test_immutable_document_precedes_changing_question_and_metadata():
    paper = {'id': 'p', 'title': 'Title A'}
    plan = {'objective': 'Question A', 'report_sections': ['A'], 'adaptive': True}
    a = extraction_messages(plan, paper, SOURCE*5000, True)
    b = extraction_messages({**plan, 'objective': 'Question B'}, {**paper, 'title': 'Title B'}, SOURCE*5000, True)
    assert a[0] == b[0]
    first, second = a[1]['content'], b[1]['content']
    difference = next(i for i, (left, right) in enumerate(zip(first, second)) if left != right)
    assert difference > 120000
    assert list(json.loads(first)) == ['content', 'source_type', 'metadata', 'research_plan']
    assert 'report_sections' not in json.loads(first)['research_plan']


def test_cache_refuses_corruption_unverified_quotes_and_document_changes(tmp_path):
    key = 'a'*64
    profile = bind_profile({'paper_id': 'p', 'evidence': [{'quote': SOURCE}]}, SOURCE, 'abstract')
    save_profile(tmp_path, key, profile)
    assert load_profile(tmp_path, key, 'p', SOURCE)['evidence'][0]['source_verified']
    assert load_profile(tmp_path, key, 'p', SOURCE+' changed') is None
    assert load_profile(tmp_path, key, 'another', SOURCE) is None
    path_for(tmp_path, key).write_text('{broken JSON', encoding='utf-8')
    assert load_profile(tmp_path, key, 'p', SOURCE) is None
    save_profile(tmp_path, key, bind_profile({'paper_id': 'p', 'evidence': [{'quote': 'invented quote long enough'}]}, SOURCE, 'abstract'))
    assert path_for(tmp_path, key).read_text() == '{broken JSON'
    with pytest.raises(ValueError): path_for(tmp_path, '../outside')


def test_linked_cache_directory_is_not_followed(tmp_path, monkeypatch):
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p.name == 'cache' or original(p))
    key = 'b'*64
    assert load_profile(tmp_path, key, 'p', SOURCE) is None
    save_profile(tmp_path, key, bind_profile({'paper_id': 'p', 'evidence': [{'quote': SOURCE}]}, SOURCE, 'abstract'))
    assert not (tmp_path/'.agent_history').exists()


def test_optional_cache_write_failure_does_not_discard_evidence(tmp_path, monkeypatch):
    def failed_replace(*args): raise OSError('Fixture write failure')
    monkeypatch.setattr(Path, 'replace', failed_replace)
    save_profile(tmp_path, 'c'*64, bind_profile({'paper_id': 'p', 'evidence': [{'quote': SOURCE}]}, SOURCE, 'abstract'))
    assert not list(tmp_path.rglob('*.tmp'))
    assert load_profile(tmp_path, 'c'*64, 'p', SOURCE) is None


def test_provider_usage_and_missing_cache_counters_have_distinct_meanings():
    deepseek = normalize_usage(NS(prompt_tokens=100, completion_tokens=80,
        prompt_cache_hit_tokens=60, prompt_cache_miss_tokens=40,
        completion_tokens_details=NS(reasoning_tokens=70)))
    compatible = normalize_usage({'prompt_tokens': 200, 'completion_tokens': 20,
                                 'prompt_tokens_details': {'cached_tokens': 100}})
    unknown_cache = normalize_usage({'prompt_tokens': 50, 'completion_tokens': 10})
    summary = summarize_usage([{'usage': deepseek}, {'usage': compatible}, {'usage': unknown_cache}], 4, 2)
    assert summary['prompt_tokens'] == 350 and summary['completion_tokens'] == 110
    assert summary['reasoning_tokens'] == 70  # Subset of completion, never added twice.
    assert summary['input_cache_hit_rate'] == pytest.approx(160/300)
    assert summary['requests_with_cache_usage'] == 2 and summary['requests_without_usage'] == 1
    assert summary['local_evidence_cache_hits'] == 2
    assert unknown_cache['prompt_cache_hit_tokens'] is None
    assert normalize_usage(None) is None
    assert normalize_usage({'prompt_tokens': True}) is None
    assert normalize_usage({'prompt_tokens': 10, 'prompt_cache_hit_tokens': 20})['prompt_cache_hit_tokens'] is None


class Stream:
    def __init__(self, chunks): self.chunks, self.closed = chunks, False
    async def __aiter__(self):
        for chunk in self.chunks: yield chunk
    async def close(self): self.closed = True


@pytest.mark.parametrize('mode', ['internal', 'public', 'truncated'])
def test_stream_records_usage_only_last_chunk_and_preserves_failure_cost(monkeypatch, mode):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    llm = DeepSeekClient()
    usage = NS(prompt_tokens=100, completion_tokens=30, prompt_cache_hit_tokens=80, prompt_cache_miss_tokens=20)
    stream = Stream([
        NS(choices=[NS(delta=NS(content='{"ok":true}', reasoning_content=None), finish_reason='length' if mode=='truncated' else 'stop')], usage=None),
        NS(choices=[], usage=usage)])
    async def create(**kw): return stream
    async def activity(data): pass
    llm.client = NS(chat=NS(completions=NS(create=create)))
    llm._ready = True
    async def run():
        if mode == 'public':
            assert ''.join([part async for part in llm.chat_stream([{'role': 'user', 'content': 'Fixture'}])]) == '{"ok":true}'
        else:
            llm.activity_callback = activity
            if mode == 'truncated':
                with pytest.raises(LLMOutputError): await llm.chat_json([{'role': 'user', 'content': 'JSON fixture'}])
            else: assert await llm.chat_json([{'role': 'user', 'content': 'JSON fixture'}]) == {'ok': True}
    asyncio.run(run())
    assert stream.closed and llm.calls == 1 and len(llm.usage_records) == 1
    assert llm.usage_summary()['input_cache_hit_rate'] == .8


def test_nonstream_usage_is_saved_and_public_job_does_not_expose_payload(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    llm = DeepSeekClient()
    async def create(**kw):
        return NS(choices=[NS(message=NS(content='{"ok":true}'), finish_reason='stop')],
                  usage=NS(prompt_tokens=100, completion_tokens=20))
    llm.client = NS(chat=NS(completions=NS(create=create)))
    llm._ready = True
    assert asyncio.run(llm.chat_json([{'role': 'user', 'content': 'JSON fixture'}])) == {'ok': True}
    from src.server.routes_jobs import public_job
    summary = llm.usage_summary()
    job = public_job({'id': 'j', 'status': 'completed', 'payload': '{"message":"private fixture"}',
                      'request_key': 'private fixture', 'result': json.dumps({'usage_summary': summary})})
    assert job['usage_summary']['completion_tokens'] == 20
    assert job['usage_summary']['input_cache_hit_rate'] is None
    assert 'payload' not in job and 'request_key' not in job and 'result' not in job
