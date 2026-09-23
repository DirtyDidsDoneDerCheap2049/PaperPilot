import asyncio
import json
from types import SimpleNamespace
import pytest
from src.llm.deepseek_client import DeepSeekClient, LLMOutputError


def test_structured_response_streams_with_bounded_thinking_preview_outside_answer(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    async def run():
        client=DeepSeekClient(base_url='http://127.0.0.1/v1',fast_model='local')
        events=[];closed=[]
        class Stream:
            async def __aiter__(self):
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None,reasoning_content='private reasoning'),finish_reason=None)])
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"summary":"first'),finish_reason=None)])
                # Consumer has received the actual first text while the provider is still producing.
                assert any(e.get('content')=='{"summary":"first' for e in events)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=' second"}'),finish_reason='stop')])
            async def close(self):closed.append(True)
        async def create(**kwargs):
            assert kwargs['stream'] is True
            return Stream()
        async def callback(data):events.append(data)
        client.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client._ready=True;client.activity_callback=callback
        value=await client.chat_json([{'role':'user','content':'Return JSON'}])
        assert value=={'summary':'first second'}
        assert ''.join(e.get('content','') for e in events)=='{"summary":"first second"}'
        assert 'private reasoning' not in json.dumps(value)
        assert all(len(e.get('preview',''))<=220 for e in events)
        assert any(e.get('phase')=='thinking' for e in events)
        assert closed==[True]
    asyncio.run(run())


def test_truncated_stream_keeps_draft_but_rejects_result(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    async def run():
        client=DeepSeekClient();events=[];closed=[]
        class Stream:
            async def __aiter__(self):
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"summary":"partial'),finish_reason='length')])
            async def close(self):closed.append(True)
        async def create(**kwargs):return Stream()
        async def callback(data):events.append(data)
        client.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)));client._ready=True;client.activity_callback=callback
        with pytest.raises(LLMOutputError):await client.chat_json([{'role':'user','content':'JSON'}])
        assert any(e.get('content') for e in events) and events[-1]['phase']=='failed'
        assert closed==[True] and client.calls==1
    asyncio.run(run())


def test_sse_replays_after_cursor_and_drains_before_terminal(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.server.routes_jobs import router
    class Tasks:
        async def rpc(self,op,**kwargs):
            if op=='get':return {'id':'j','status':'succeeded','session_id':'s','token':1,'result':'{}','payload':'{}'}
            return [dict(seq=i,body=json.dumps({'type':'model_delta','content':str(i)})) for i in [1,2,3] if i>kwargs['after']]
    app=FastAPI();app.state.tasks=Tasks();app.include_router(router)
    response=TestClient(app).get('/api/jobs/j/stream?after=1',headers={'Last-Event-ID':'2'})
    assert response.status_code==200
    assert 'id: 3' in response.text and 'id: 2' not in response.text
    assert response.text.index('id: 3')<response.text.index('"kind": "status"')


def test_reading_edition_preserves_evidence_and_no_fake_conclusion():
    from src.analysis.report_layout import reading_edition
    old='# Original\n\n## 4. 通过证据门与 Critic 的窄义候选\nNo supported candidate\n## 5. 未进入正式结论的候选\nDraft with evidence E1\n## 7. 推荐下一步\nRead Paper A\n## 3. 逐篇证据审计与已覆盖路线\nEvidence E1 exact quote\n'
    text=reading_edition(old,{'research_question':'Test question'},{'gaps':[],'provisional_gaps':[{}],'critic':{'skipped':True},'reliability':{'warnings':['Some evidence is missing']}},[],[],[])
    assert '尚不能确认' in text and '未进行额外模型复核' in text
    assert 'Evidence E1 exact quote' in text and 'Draft with evidence E1' in text
    assert 'Some evidence is missing' in text
    assert text.index('推荐下一步')<text.index('材料与审计附录')
