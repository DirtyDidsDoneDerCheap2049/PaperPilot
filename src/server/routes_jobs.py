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
    data['usage_summary']=result.get('usage_summary')
    if data['mode']=='library_organize':
        data['organization']=result
    if data['mode']=='library_index':
        data['index_result']=result
    return data

async def submit_chat(req,request):
    if req.mode not in {'analysis','report_chat'}:
        raise HTTPException(400,'不支持的任务类型')
    from src.analysis.report_revision import edit_signal, resolve_report_request
    if req.mode=='report_chat' and len(set(req.selected_paper_ids))>8 and edit_signal(req.message) not in {'revision','ambiguous','research'}:
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
    # Reuse a pinned revision on an idempotent HTTP retry. Never choose the
    # latest report again or repeat an intent-model request for the same key.
    for existing in await call(request,'list'):
        if existing.get('request_key') == key:
            old=json.loads(existing.get('payload') or '{}')
            expected={**req.model_dump(),'session_id':sid}
            if any(old.get(k) != v for k,v in expected.items() if k != 'mode') or old.get('requested_mode',old.get('mode')) != req.mode:
                raise HTTPException(409,'idempotency_conflict')
            return {'session_id':sid,'message_id':old.get('message_id'),'job_id':existing['id'],'status':'accepted','mode':old.get('mode',req.mode)}
    if await service(request).active_session(sid):
        raise HTTPException(409,'请先取消或等待当前任务结束')
    try:
        source=await resolve_report_request(request.app.state.db,request.app.state.llm,
            request.app.state.workspace_root,sid,req.message,req.report_path)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    if source and source.get('operation'):
        payload.update(mode=source['operation'],requested_mode=req.mode)
    elif source:
        payload.update(mode='report_revision',requested_mode=req.mode,revision_source=source)
    if payload['mode']=='report_chat' and len(set(req.selected_paper_ids))>8:
        raise HTTPException(400,'讨论结果最多附带 8 篇论文片段；批量阅读与跨论文分析请切换研究 Agent，不会静默忽略多选论文。')
    from src.runtime.research_limits import research_limits
    payload['research_limits']=research_limits(request.app.state.config)
    payload.update(session_id=sid,message_id=mid)
    job=await call(request,'submit',id=str(uuid.uuid4()),session_id=sid,request_key=key,payload=payload)
    return {'session_id':sid,'message_id':mid,'job_id':job['id'],'status':'accepted','mode':payload['mode']}

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
    existing=public_job(await call(request,'get',id=job_id))
    if existing.get('mode')!='library_index' and not request.app.state.llm._ready:
        raise HTTPException(409,'请先配置模型 API')
    return public_job(await call(request,'retry',id=job_id))


@router.get('/api/jobs/{job_id}/stream')
async def stream_events(job_id: str, request: Request, after: int = 0, batch: int = 1):
    if not 1 <= batch <= 200:
        raise HTTPException(400, '无效的事件批次大小')
    await call(request, 'get', id=job_id)
    try:
        cursor = max(0, after, int(request.headers.get('last-event-id', '0')))
    except ValueError:
        raise HTTPException(400, '无效的事件位置')
    async def generate():
        nonlocal cursor
        previous = None
        while not await request.is_disconnected():
            tasks = service(request)
            revision = getattr(tasks, 'revision', 0)
            job = public_job(await call(request, 'get', id=job_id))
            # Read status before draining: a terminal snapshot cannot precede its final events.
            while True:
                rows = await call(request, 'events', id=job_id, after=cursor)
                for start in range(0, len(rows), batch):
                    page = rows[start:start+batch]
                    cursor = page[-1]['seq']
                    frame = {'kind':'event','row':page[0]} if batch == 1 else {'kind':'events','rows':page}
                    yield f'id: {cursor}\ndata: {json.dumps(frame,ensure_ascii=False)}\n\n'
                if len(rows) < 200:
                    break
            snapshot = json.dumps({'kind':'status','job':job}, ensure_ascii=False)
            if snapshot != previous:
                yield f'data: {snapshot}\n\n'
                previous = snapshot
            if job['status'] not in {'queued','running','cancel_requested'}:
                return
            yield ': connected\n\n'
            if hasattr(tasks, 'wait_for_update'):
                await tasks.wait_for_update(revision, timeout=5)
            else:
                await asyncio.sleep(.2)
    return StreamingResponse(generate(), media_type='text/event-stream',
                             headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@router.post('/api/jobs/{job_id}/cancel')
async def cancel(job_id:str,request:Request):
    return public_job(await call(request,'cancel',id=job_id))
