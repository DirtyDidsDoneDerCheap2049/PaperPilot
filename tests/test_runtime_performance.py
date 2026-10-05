"""Offline regressions for overlap, request identity and event latency."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.runtime.parallel import ordered_map
from src.runtime.tasks import TaskService, native_binary
from src.llm.deepseek_client import DeepSeekClient, LLMStopError
from src.llm.request_context import model_context


def test_bounded_work_keeps_order_and_drains_cancelled_siblings():
    async def run():
        active = peak = 0
        async def operation(value):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(.01 * (4-value))
                return value
            finally:
                active -= 1
        assert await ordered_map(range(4), operation, 3) == list(range(4))
        assert peak == 3 and active == 0
        started, closed = [], []
        async def fail(value):
            started.append(value)
            try:
                if value == 0:
                    await asyncio.sleep(.01)
                    raise RuntimeError('terminal')
                await asyncio.sleep(10)
            finally:
                closed.append(value)
        with pytest.raises(RuntimeError):
            await ordered_map(range(100), fail, 3)
        assert set(started) == set(closed) == {0, 1, 2}
    asyncio.run(run())


def test_concurrent_streams_keep_call_and_paper_identity(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    async def run():
        client = DeepSeekClient()
        events, closed = [], []
        class Stream:
            def __init__(self, text): self.text = text
            async def __aiter__(self):
                await asyncio.sleep(.01)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=self.text, reasoning_content='thinking'),finish_reason='stop')])
            async def close(self): closed.append(self.text)
        async def create(**kw): return Stream(kw['messages'][0]['content'])
        async def activity(event): events.append(event)
        client.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client._ready=True;client.activity_callback=activity
        async def request(label):
            with model_context(agent='QuestionExtractor', item_label=label, paper_id=label):
                return await client._chat_impl([{'role':'user','content':label}],client.fast_model)
        assert await ordered_map(['a','b','c'],request,3)==['a','b','c']
        assert client.calls==3 and set(closed)=={'a','b','c'}
        for call_id,label in enumerate(['a','b','c'],1):
            rows=[e for e in events if e['call']==call_id]
            assert rows and all(e['paper_id']==label and e['item_label']==label for e in rows)
            assert ''.join(e.get('content','') for e in rows)==label
            final=next(e for e in rows if e['phase']=='received')
            assert final['first_content_ms']<=final['duration_ms']
        client.max_calls=3
        with pytest.raises(LLMStopError): await request('unstarted')
        assert client.calls==3
    asyncio.run(run())


def test_native_commit_wakes_subscriber_even_when_commit_precedes_wait(tmp_path):
    async def run():
        service=TaskService(tmp_path,None,None)
        service.process=await asyncio.create_subprocess_exec(str(native_binary()),str(tmp_path/'tasks.db'),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            await service.rpc('submit',id='j',session_id='s',request_key='k',payload={'message':'fixture'})
            job=await service.rpc('claim')
            revision=service.revision
            waiter=asyncio.create_task(service.wait_for_update(revision,timeout=10))
            await service.rpc('event',id='j',token=job['token'],body={'type':'model_delta','content':'fixture'})
            await asyncio.wait_for(waiter,.5)
            # A commit while SSE is draining must not be lost before wait starts.
            await asyncio.wait_for(service.wait_for_update(revision,timeout=10),.5)
            rows=await service.rpc('events',id='j',after=0)
            assert len(rows)==1
        finally:
            service.process.stdin.close()
            await service.process.wait()
    asyncio.run(run())


def test_question_extraction_overlaps_but_preserves_evidence_and_cache(tmp_path):
    from src.analysis.question_evidence import extract_question_profiles, extraction_key
    from src.analysis.research_plan import plan_for
    from src.analysis.provenance import bind_profile
    from src.agents.base import AgentContext, AgentResult
    papers=[{'id':str(i),'title':'Fixture '+str(i),'abstract':'Exact source quote '+str(i)} for i in range(6)]
    direction={'research_plan':{'task_type':'literature_review','research_question':'Offline fixture'}}
    plan=plan_for(direction)
    cached=bind_profile({'paper_id':'0','evidence':[{'quote':papers[0]['abstract']}],
                        'question_extraction_key':extraction_key(plan)},papers[0]['abstract'],'abstract')
    active=peak=calls=0
    class Model:
        async def chat_json(self,messages):
            nonlocal active,peak,calls
            calls+=1;active+=1;peak=max(peak,active)
            try:
                data=json.loads(messages[-1]['content'])
                await asyncio.sleep(.01*(6-int(data['metadata']['id'])))
                return {'method_summary':'Fixture','evidence':[{'quote':data['content'],'section':'Abstract','supports':'Fixture'}]}
            finally:active-=1
    async def progress(*args):pass
    owner=SimpleNamespace(llm=Model(),db=SimpleNamespace(fetchone=lambda *a:{}),_progress=progress,
        _verified_cached_profile=lambda pid,text:cached if pid=='0' else None)
    async def run():
        return await extract_question_profiles(owner,AgentContext(workspace_root=tmp_path,config={}),
            AgentResult(status='completed',data={'metadata_only':[p['id'] for p in papers]}),papers,direction)
    profiles=asyncio.run(run())
    assert [p['paper_id'] for p in profiles]==[p['id'] for p in papers]
    assert calls==5 and peak==3 and active==0
    assert all(p['evidence'][0]['source_verified'] for p in profiles)


def test_external_cancellation_joins_running_operations():
    async def run():
        closed=[]
        entered=asyncio.Event()
        async def operation(i):
            entered.set()
            try:await asyncio.sleep(10)
            finally:closed.append(i)
        batch=asyncio.create_task(ordered_map(range(30),operation,3))
        await entered.wait();batch.cancel()
        with pytest.raises(asyncio.CancelledError):await batch
        assert set(closed)=={0,1,2}
    asyncio.run(run())


def test_large_fast_stream_coalesces_without_losing_output(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    clock=[1000.0]
    monkeypatch.setattr('src.llm.deepseek_client.time.monotonic',lambda:clock[0])
    async def run():
        client=DeepSeekClient();events=[];parts=['x'*100 for _ in range(1200)]
        class Stream:
            async def __aiter__(self):
                for part in parts:
                    clock[0]+=.0001
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=part,reasoning_content='r'*300),finish_reason=None)])
            async def close(self):pass
        async def create(**kwargs):
            assert kwargs['reasoning_effort']=='max'
            return Stream()
        async def activity(event):events.append(event)
        client._ready=True;client.activity_callback=activity
        client.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        answer=await client._chat_impl([{'role':'user','content':'offline'}],client.fast_model)
        deltas=[e for e in events if e['phase']=='delta']
        assert answer==''.join(parts)==''.join(e['content'] for e in deltas)
        # Dense provider chunks stay coalesced while permitting responsive live updates.
        assert len(deltas)<len(parts)//20 and len(events)<50
        assert all(len(e.get('preview',''))<=220 for e in events)
    asyncio.run(run())


def test_audit_batches_overlap_without_losing_or_reordering_papers():
    from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
    class Analyzer(EvidenceGroundedAnalyzer):
        active=peak=0
        async def _audit_batch(self,direction,claims,batch):
            self.active+=1;self.peak=max(self.peak,self.active)
            try:
                await asyncio.sleep(.01)
                return [dict(p,assessment_status='audited',claim_assessments=[]) for p in batch]
            finally:self.active-=1
        async def _chat_json(self,messages):return {'gaps':[]}
    analyzer=Analyzer(SimpleNamespace(parallel_papers=3),batch_size=2)
    profiles=[{'paper_id':str(i),'source_type':'paper_abstract','evidence':[{'quote':'Fixture'}]} for i in range(12)]
    answer=asyncio.run(analyzer.analyze({'research_question':'Offline fixture'},profiles))
    assert [p['paper_id'] for p in answer['coverage_audit']]==[str(i) for i in range(12)]
    assert analyzer.peak==3 and analyzer.active==0
