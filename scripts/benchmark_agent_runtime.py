"""Offline scheduling/native-event benchmark, never sends papers to an API.

Fixed latency models isolate local scheduling overhead. Results are not a
DeepSeek throughput or scientific-accuracy measurement.
"""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.agents.base import AgentContext, AgentResult
from src.analysis.question_evidence import extract_question_profiles
from src.runtime.tasks import TaskService, native_binary


async def reading(limit):
    active=peak=calls=0
    class FixedLatencyModel:
        async def chat_json(self,messages):
            nonlocal active,peak,calls
            calls+=1;active+=1;peak=max(peak,active)
            try:
                data=json.loads(messages[-1]['content'])
                await asyncio.sleep(.05)
                return {'method_summary':'Offline scheduling fixture',
                        'evidence':[{'quote':data['content'],'section':'Abstract','supports':'Fixture'}]}
            finally:active-=1
    async def progress(*a):pass
    owner=SimpleNamespace(llm=FixedLatencyModel(),db=SimpleNamespace(fetchone=lambda *a:{}),
        _verified_cached_profile=lambda *a:None,_progress=progress)
    papers=[{'id':str(i),'title':'Offline '+str(i),'abstract':'Exact offline source '+str(i)} for i in range(30)]
    start=time.perf_counter()
    profiles=await extract_question_profiles(owner,AgentContext(workspace_root=ROOT,config={'research':{'parallel_papers':limit}}),
        AgentResult(status='completed',data={'metadata_only':[p['id'] for p in papers]}),papers,
        {'research_plan':{'task_type':'literature_review','research_question':'Offline fixture'}})
    duration=time.perf_counter()-start
    assert [p['paper_id'] for p in profiles]==[p['id'] for p in papers]
    assert all(p['evidence'][0]['source_verified'] for p in profiles)
    return {'seconds':round(duration,4),'calls':calls,'peak_in_flight':peak,'verified_profiles':len(profiles)}


async def native_delivery():
    parent=ROOT/'work/testing/current';parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='latency-',dir=parent) as temporary:
        service=TaskService(Path(temporary),None,None)
        service.process=await asyncio.create_subprocess_exec(str(native_binary()),str(Path(temporary)/'tasks.db'),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        samples=[]
        try:
            await service.rpc('submit',id='j',session_id='s',request_key='k',payload={'message':'Offline fixture'})
            token=(await service.rpc('claim'))['token']
            for i in range(50):
                revision=service.revision
                waiter=asyncio.create_task(service.wait_for_update(revision))
                start=time.perf_counter()
                await service.rpc('event',id='j',token=token,body={'type':'model_delta','content':'fixture','call':1})
                await waiter
                await service.rpc('events',id='j',after=i)
                samples.append((time.perf_counter()-start)*1000)
        finally:
            service.process.stdin.close();await service.process.wait()
        return {'samples':len(samples),'median_ms':round(statistics.median(samples),3),
                'p95_ms':round(sorted(samples)[int(len(samples)*.95)-1],3),
                'scope':'native FULL-sync commit + notification + event read; excludes WebView/provider'}


async def main(args):
    serial=[await reading(1) for _ in range(3)]
    parallel=[await reading(3) for _ in range(3)]
    record={'kind':'offline fixed-latency scheduling benchmark','provider_requests':0,
        'serial':serial,'parallel':parallel,'native_delivery':await native_delivery()}
    record['median_seconds']={'serial':statistics.median(r['seconds'] for r in serial),
                              'parallel':statistics.median(r['seconds'] for r in parallel)}
    if args.output:
        target=Path(args.output).resolve()
        if not target.is_relative_to(ROOT/'work'):raise ValueError('Benchmark evidence belongs under work')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output')
    asyncio.run(main(parser.parse_args()))
