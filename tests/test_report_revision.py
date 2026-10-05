import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.agents.base import AgentContext
from src.agents.orchestrator import Orchestrator
from src.analysis.report_revision import edit_signal, load_report_source, resolve_report_request, split_references, validate_revision
from src.knowledge.sqlite_store import SQLiteStore
from src.server.routes_chat import ChatRequest
from src.server.routes_jobs import submit_chat

OLD = '''# 检索方法的研究空白

## 结论
可研究跨域证据加权，但效果尚未知 [1]。

## 候选研究空白
### 1. 跨域证据加权（待核实）
输入内有直接依据，但尚缺跨域实验结果，不能确认收益 [1]。

## 已有工作
论文只报告单域结果，指标为0.72 [1]。

## 本轮边界
已读1篇，另1篇缺全文。

## 参考文献
- [1] [Evidence weighting](https://example.org/paper)（全文）
'''
NEW = OLD.split('## 参考文献')[0].replace('（待核实）', '').replace('可研究跨域证据加权，但效果尚未知', '优先研究跨域证据加权；现有单域结果提供依据，跨域收益尚无实验支持')


def seed(tmp_path):
    db = SQLiteStore(tmp_path / 'db/ai_reader.db')
    db.init_schema()
    db.execute("INSERT INTO sessions(id,workspace_id,title) VALUES('s','w','检索研究')")
    (tmp_path / 'reports').mkdir()
    file = tmp_path / 'reports/original.md'
    file.write_text(OLD, encoding='utf-8')
    log = {'task_type': 'gap_discovery', 'papers_found': 2, 'provisional_gaps': [{'name': '跨域证据加权', 'candidate_status': 'insufficient_evidence'}]}
    db.execute('INSERT INTO gap_analyses(id,session_id,direction,search_log_json,gaps_json,evidence_json,report_path) VALUES(?,?,?,?,?,?,?)',
               ('old', 's', '{"research_question":"跨域检索有哪些空白"}', json.dumps(log), '[]', '[{"quote":"0.72"}]', str(file)))
    return db, file


class Model:
    reasoning_model = 'fake'
    def __init__(self, verdict=True):
        self.calls = []
        self.verdict = verdict
    async def chat(self, messages, **kwargs):
        self.calls.append(('draft', messages))
        assert 'Naturawrite' in messages[0]['content']
        return NEW
    async def chat_json(self, messages, **kwargs):
        self.calls.append(('review', messages))
        return {'pass': self.verdict, 'issues': [] if self.verdict else ['错误修改事实']}


@pytest.mark.parametrize('message,signal', [
    ('不要重新检索，只修改报告，让它好读些', 'revision'),
    ('不重新阅读论文，把结论精简一下', 'revision'),
    ('只改报告，不要重新研究', 'revision'),
    ('重新写报告，保留事实', 'revision'),
    ('润色报告，并补查最新论文', 'research'),
    ('重新阅读选中的论文', 'research'),
    ('为什么都是待核实', 'ambiguous'),
    ('太难读了，重点直接写出来', 'ambiguous'),
    ('找跨域检索的新论文并研究空白', 'other'),
])
def test_local_request_signals(message, signal):
    assert edit_signal(message) == signal


@pytest.mark.parametrize('action,expected', [('report_revision', 'source'), ('research', 'analysis'), ('discussion', 'report_chat')])
def test_ambiguous_request_uses_model_without_sending_report_body(tmp_path, action, expected):
    db, _ = seed(tmp_path)
    class Router:
        async def chat_json(self, messages, **kwargs):
            assert '0.72' not in messages[-1]['content']
            return {'action': action}
    try:
        value = asyncio.run(resolve_report_request(db, Router(), tmp_path, 's', '太难读了'))
        assert ('text' in value) if expected == 'source' else value['operation'] == expected
    finally:
        db.close()


def test_no_report_cannot_silently_start_research(tmp_path):
    db, _ = seed(tmp_path)
    try:
        with pytest.raises(ValueError, match='没有可修改'):
            asyncio.run(resolve_report_request(db, None, tmp_path, 'other-session', '修改报告'))
        with pytest.raises(HTTPException):
            load_report_source(db, tmp_path, 's', '../outside.md')
    finally:
        db.close()


def test_revision_uses_saved_findings_without_reading_papers_and_retries_once(tmp_path, monkeypatch):
    db, file = seed(tmp_path)
    model = Model()
    owner = Orchestrator(db, model, None, None)
    ctx = AgentContext(session_id='s', workspace_id='w', workspace_root=tmp_path)
    source = load_report_source(db, tmp_path, 's')
    async def forbidden(*args, **kwargs):
        raise AssertionError('Revision must not search, parse or extract papers')
    for name in ('_parse_direction', '_search', '_extract_innovations'):
        monkeypatch.setattr(owner, name, forbidden)
    try:
        payload = {'mode': 'report_revision', 'message': '只修改报告，写得自然些', 'message_id': 'edit', 'revision_source': source, 'selected_paper_ids': ['missing-pdf']}
        result = asyncio.run(owner.run(ctx, payload))
        assert result.status == 'completed', result.error
        assert result.data['new_papers_read'] == result.data['new_searches'] == 0
        assert len(model.calls) == 2
        assert file.read_text(encoding='utf-8') == OLD
        assert split_references(result.data['report_content'])[1] == split_references(OLD)[1]
        saved = db.fetchone('SELECT * FROM gap_analyses WHERE id!=?', ('old',))
        assert json.loads(saved['search_log_json'])['provisional_gaps'] == source['analysis']['provisional_gaps']
        assert saved['gaps_json'] == '[]'
        next_source = load_report_source(db, tmp_path, 's')
        assert next_source['path'] == result.data['report_path']
        assert next_source['analysis']['provisional_gaps'] == source['analysis']['provisional_gaps']
        repeated = asyncio.run(owner.run(ctx, payload))
        assert repeated.data['report_path'] == result.data['report_path']
        assert len(model.calls) == 2
        assert db.fetchone('SELECT count(*) n FROM gap_analyses')['n'] == 2
        assert db.fetchone("SELECT count(*) n FROM messages WHERE role='user'")['n'] == 1
    finally:
        db.close()


def test_failed_review_leaves_no_new_report(tmp_path):
    db, file = seed(tmp_path)
    owner = Orchestrator(db, Model(False), None, None)
    try:
        result = asyncio.run(owner.run(AgentContext(session_id='s', workspace_root=tmp_path), {
            'mode': 'report_revision', 'message': '精简报告', 'message_id': 'edit', 'revision_source': load_report_source(db, tmp_path, 's')}))
        assert result.status == 'failed' and '原报告保留' in result.error
        assert list((tmp_path / 'reports').iterdir()) == [file]
        assert db.fetchone('SELECT count(*) n FROM gap_analyses')['n'] == 1
    finally:
        db.close()


def test_revision_caps_transport_retries_and_restores_client_budget(tmp_path):
    db, file = seed(tmp_path)
    class RetryingModel:
        reasoning_model = 'fake'
        calls = 0
        max_calls = 100
        async def chat(self, *args, **kwargs):
            while self.calls < self.max_calls:
                self.calls += 1  # A failing transport implementation attempts retries.
            raise RuntimeError('request budget exhausted')
    model = RetryingModel()
    try:
        result = asyncio.run(Orchestrator(db, model, None, None).run(AgentContext(session_id='s', workspace_root=tmp_path), {
            'mode': 'report_revision', 'message': '精简报告', 'message_id': 'edit', 'revision_source': load_report_source(db, tmp_path, 's')}))
        assert result.status == 'failed'
        assert model.calls == 4 and model.max_calls == 100
        assert file.read_text(encoding='utf-8') == OLD
        assert db.fetchone('SELECT count(*) n FROM gap_analyses')['n'] == 1
    finally:
        db.close()


def test_source_edit_during_generation_is_not_overwritten(tmp_path):
    db, file = seed(tmp_path)
    source = load_report_source(db, tmp_path, 's')
    file.write_text(OLD + '\n用户修改', encoding='utf-8')
    try:
        result = asyncio.run(Orchestrator(db, Model(), None, None).run(AgentContext(session_id='s', workspace_root=tmp_path), {
            'mode': 'report_revision', 'message': '精简报告', 'message_id': 'edit', 'revision_source': source}))
        assert result.status == 'failed' and '发生变化' in result.error
        assert file.read_text(encoding='utf-8').endswith('用户修改')
    finally:
        db.close()


@pytest.mark.parametrize('bad', [NEW.replace('[1]', '[99]'), NEW.replace('[1]', ''), NEW + '\nexhaustive=false', NEW + '\n## 参考文献\nfake', NEW + '\nhttps://fake.org'])
def test_invalid_citations_and_internal_or_reference_edits_are_rejected(bad):
    assert validate_revision(bad, {'text': OLD})


def test_submit_pins_report_and_idempotent_retry_without_model_call(tmp_path):
    db, file = seed(tmp_path)
    class Tasks:
        def __init__(self): self.items = []
        async def active_session(self, sid): return None
        async def rpc(self, op, **kwargs):
            if op == 'list': return self.items
            assert op == 'submit'
            job = {**kwargs, 'payload': json.dumps(kwargs['payload']), 'status': 'queued'}
            self.items.append(job)
            return job
    tasks = Tasks()
    state = SimpleNamespace(db=db, llm=SimpleNamespace(_ready=True), workspace_root=tmp_path, config={}, tasks=tasks)
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={'Idempotency-Key': 'edit-key'})
    req = ChatRequest(session_id='s', mode='report_chat', message='不要重新检索，只精简报告', selected_paper_ids=['p'] * 10)
    try:
        result = asyncio.run(submit_chat(req, request))
        assert result['mode'] == 'report_revision'
        pinned = json.loads(tasks.items[0]['payload'])['revision_source']
        assert pinned['text'] == OLD
        file.write_text(OLD + '\nchanged later', encoding='utf-8')
        duplicate = asyncio.run(submit_chat(req, request))
        assert duplicate['job_id'] == result['job_id'] and len(tasks.items) == 1
        with pytest.raises(HTTPException) as error:
            asyncio.run(submit_chat(req.model_copy(update={'message': '另一条请求'}), request))
        assert error.value.status_code == 409
    finally:
        db.close()
