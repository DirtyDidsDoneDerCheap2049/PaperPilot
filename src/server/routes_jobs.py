import json
import uuid
import asyncio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from src.runtime.tasks import TaskError

router=APIRouter()

def service(request):
    tasks=getattr(request.app.state,'tasks',None)
    if not tasks:
        raise HTTPException(503,'任务服务未启动')
    return tasks

async def call(request,op,**args):
    try:
        return await service(request).rpc(op,**args)
    except TaskError as exc:
        text=str(exc)
        code=404 if text=='not_found' else 429 if text=='queue_full' else 409 if text in {'conflict','idempotency_conflict','not_retryable','terminal_job'} else 503
        raise HTTPException(code,text) from exc

def public_job(job):
    data={k:v for k,v in job.items() if k not in {'payload','request_key','result'}}
    result=json.loads(job.get('result') or '{}')
    data['report_path']=result.get('report_path_rel')
    payload=json.loads(job.get('payload') or '{}')
    data['mode']=payload.get('mode','analysis')
    data['message_id']=payload.get('message_id')
    data['research_limits']=payload.get('research_limits')
    return data

async def submit_chat(req,request):
    if req.mode not in {'analysis','report_chat'}:
        raise HTTPException(400,'不支持的任务类型')
    if req.mode=='report_chat' and len(set(req.selected_paper_ids))>8:
        raise HTTPException(400,'讨论结果最多附带 8 篇论文片段；批量阅读与跨论文分析请切换研究 Agent，不会静默忽略多选论文。')
    if req.report_path:
        from src.server.routes_chat import _safe_report_file
        _safe_report_file(request.app.state.workspace_root,req.report_path)
    if not request.app.state.llm._ready:
        raise HTTPException(409,'请先在设置中配置模型服务地址、模型和 API Key')
    key=request.headers.get('Idempotency-Key') or str(uuid.uuid4())
    if len(key)>128:
        raise HTTPException(400,'幂等键过长')
    sid=req.session_id or str(uuid.uuid5(uuid.NAMESPACE_URL,key))
    mid=str(uuid.uuid5(uuid.NAMESPACE_URL,sid+':'+key))
    payload=req.model_dump()
    from src.runtime.research_limits import research_limits
    payload['research_limits']=research_limits(request.app.state.config)
    payload.update(session_id=sid,message_id=mid)
    job=await call(request,'submit',id=str(uuid.uuid4()),session_id=sid,request_key=key,payload=payload)
    return {'session_id':sid,'message_id':mid,'job_id':job['id'],'status':'accepted','mode':req.mode}

@router.get('/api/jobs')
async def jobs(request:Request):
    return {'jobs':[public_job(j) for j in await call(request,'list')]}

@router.get('/api/jobs/{job_id}')
async def job(job_id:str,request:Request):
    return public_job(await call(request,'get',id=job_id))

@router.get('/api/jobs/{job_id}/events')
async def events(job_id:str,request:Request,after:int=0):
    return {'events':await call(request,'events',id=job_id,after=after)}

@router.post('/api/jobs/{job_id}/retry')
async def retry(job_id:str,request:Request):
    if not request.app.state.llm._ready:
        raise HTTPException(409,'请先配置模型 API')
    return public_job(await call(request,'retry',id=job_id))


@router.get('/api/jobs/{job_id}/stream')
async def stream_events(job_id: str, request: Request, after: int = 0):
    await call(request, 'get', id=job_id)
    try:
        cursor = max(0, after, int(request.headers.get('last-event-id', '0')))
    except ValueError:
        raise HTTPException(400, '无效的事件位置')
    async def generate():
        nonlocal cursor
        previous = None
        while not await request.is_disconnected():
            job = public_job(await call(request, 'get', id=job_id))
            # Read status before draining: a terminal snapshot cannot precede its final events.
            while True:
                rows = await call(request, 'events', id=job_id, after=cursor)
                for row in rows:
                    cursor = row['seq']
                    yield f'id: {cursor}\ndata: {json.dumps({"kind":"event","row":row},ensure_ascii=False)}\n\n'
                if len(rows) < 200:
                    break
            snapshot = json.dumps({'kind':'status','job':job}, ensure_ascii=False)
            if snapshot != previous:
                yield f'data: {snapshot}\n\n'
                previous = snapshot
            if job['status'] not in {'queued','running','cancel_requested'}:
                return
            yield ': connected\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(generate(), media_type='text/event-stream',
                             headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@router.post('/api/jobs/{job_id}/cancel')
async def cancel(job_id:str,request:Request):
    return public_job(await call(request,'cancel',id=job_id))
