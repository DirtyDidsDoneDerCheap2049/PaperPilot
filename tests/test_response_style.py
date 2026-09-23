import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest

from src.llm.deepseek_client import DeepSeekClient, LLMOutputError
from src.llm.response_style import MARKER


def client_with_responses(monkeypatch, responses):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    client = DeepSeekClient()
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        content = next(responses)
        if kwargs['stream']:
            async def chunks():
                yield NS(choices=[])
                for text in content:
                    yield NS(choices=[NS(delta=NS(content=text))])
            return chunks()
        return NS(choices=[NS(message=NS(content=content))])

    client.client = NS(chat=NS(completions=NS(create=create)))
    client._ready = True
    return client, requests


def test_chat_preserves_history_and_source_text(monkeypatch):
    client, requests = client_with_responses(monkeypatch, iter(['证据不足 [P1]']))
    messages = [{'role': 'system', 'content': '保留引用。'},
                {'role': 'user', 'content': 'P1: "accuracy = 0.72"'}]
    original = deepcopy(messages)
    assert asyncio.run(client.chat(messages)) == '证据不足 [P1]'
    assert messages == original
    sent = requests[0]['messages']
    assert sent[0]['content'].startswith('保留引用。')
    assert MARKER in sent[0]['content']
    assert sent[1] == messages[1]
    assert len(requests) == 1


def test_insufficient_balance_is_clear_and_not_retried(monkeypatch):
    client, requests = client_with_responses(monkeypatch, iter([]))
    class BalanceError(Exception): status_code=402
    async def create(**kwargs):
        requests.append(kwargs)
        raise BalanceError('Insufficient Balance')
    client.client.chat.completions.create=create
    with pytest.raises(LLMOutputError,match='余额不足'):
        asyncio.run(client.chat([{'role':'user','content':'test'}]))
    assert len(requests)==1


def test_stream_applies_guidance_without_rewriting_deltas(monkeypatch):
    client, requests = client_with_responses(monkeypatch, iter([['尚不能', '判断 [P2]']]))
    messages = [{'role': 'user', 'content': '继续分析'}]
    async def consume():
        return [text async for text in client.chat_stream(messages)]
    assert asyncio.run(consume()) == ['尚不能', '判断 [P2]']
    assert requests[0]['messages'][0]['role'] == 'system'
    assert MARKER in requests[0]['messages'][0]['content']
    assert messages == [{'role': 'user', 'content': '继续分析'}]


def test_json_repair_keeps_schema_guidance_and_exact_evidence(monkeypatch):
    valid = '{"status":"insufficient","quote":"accuracy = 0.72","paper_id":"P1"}'
    client, requests = client_with_responses(monkeypatch, iter(['{bad', valid]))
    messages = [{'role': 'system', 'content': 'JSON: status, quote, paper_id。'},
                {'role': 'user', 'content': '原文：accuracy = 0.72'}]
    original = deepcopy(messages)
    assert asyncio.run(client.chat_json(messages)) == {
        'status': 'insufficient', 'quote': 'accuracy = 0.72', 'paper_id': 'P1'}
    assert messages == original
    assert len(requests) == 2
    for request in requests:
        policy = request['messages'][0]['content']
        assert policy.count(MARKER) == 1
        assert 'JSON: status, quote, paper_id。' in policy
        assert '不要翻译或重命名 JSON 键' in policy
        assert request['messages'][1] == messages[1]


@pytest.mark.parametrize('finish,content', [('length', 'partial'), ('length', ''), ('stop', '  '), ('content_filter', '')])
def test_incomplete_response_fails_without_paid_retry(monkeypatch, finish, content):
    client, requests = client_with_responses(monkeypatch, iter([]))
    async def create(**kwargs):
        requests.append(kwargs)
        return NS(choices=[NS(message=NS(content=content), finish_reason=finish)])
    client.client.chat.completions.create = create
    with pytest.raises(LLMOutputError):
        asyncio.run(client.chat([{'role': 'user', 'content': 'test'}]))
    assert len(requests) == 1


def test_truncated_stream_raises_after_partial_text_and_closes(monkeypatch):
    client, requests = client_with_responses(monkeypatch, iter([]))
    class Stream:
        closed = False
        def __aiter__(self):
            async def chunks():
                yield NS(choices=[NS(delta=NS(content='partial'), finish_reason=None)])
                yield NS(choices=[NS(delta=NS(content=None), finish_reason='length')])
            return chunks()
        async def close(self):
            self.closed = True
    stream = Stream()
    async def create(**kwargs):
        return stream
    client.client.chat.completions.create = create
    seen = []
    async def consume():
        async for part in client.chat_stream([{'role': 'user', 'content': 'test'}]):
            seen.append(part)
    with pytest.raises(LLMOutputError):
        asyncio.run(consume())
    assert seen == ['partial']
    assert stream.closed
