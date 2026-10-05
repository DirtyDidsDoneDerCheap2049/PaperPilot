"""Desktop library organization endpoints; long work uses the durable task queue."""
import math
import uuid
from fastapi import APIRouter, HTTPException, Request, Query
from src.knowledge.library_organizer import (BATCH_SIZE, LIBRARY_SESSION, MAX_PAPERS,
                                           decode, undo_organization, snapshot, incremental_preview)
from src.server.routes_jobs import call

router = APIRouter()


@router.get('/api/retrieval/status')
async def retrieval_status(request: Request):
    return request.app.state.retriever.status()


@router.get('/api/retrieval/search')
async def search_fulltext(request: Request, q: str=Query(min_length=1,max_length=2000), paper_ids: str=''):
    import asyncio
    from src.knowledge.paper_retrieval import library_papers
    allowed={p['id'] for p in library_papers(request.app.state.db)}
    if paper_ids:allowed &= set(paper_ids.split(',')[:100])
    hits=await asyncio.to_thread(request.app.state.retriever.search,q,sorted(allowed),8)
    return {'hits':hits,'index':request.app.state.retriever.status()}


@router.post('/api/retrieval/index', status_code=202)
async def submit_fulltext_index(request: Request):
    if not request.app.state.retriever.encoder.available:
        raise HTTPException(409, '本地向量模型缺失，请重新安装完整软件包')
    key=request.headers.get('Idempotency-Key') or str(uuid.uuid4())
    if len(key)>100:raise HTTPException(400,'幂等键过长')
    payload={'mode':'library_index','session_id':'__paper_index__','research_limits':{'timeout_minutes':120}}
    job=await call(request,'submit',id=str(uuid.uuid4()),session_id='__paper_index__',request_key='index:'+key,payload=payload)
    return {'job_id':job['id'],'status':job['status']}


@router.get('/api/library/organization')
async def organization_info(request: Request):
    db = request.app.state.db
    rows = db.fetchall("SELECT p.id,p.title,o.category,o.canonical_id,o.tags_json,o.facets_json FROM papers p LEFT JOIN paper_organization o ON o.paper_id=p.id WHERE p.id NOT LIKE 'prior:%' AND COALESCE(p.retrieval_status,'')!='off_topic'")
    categories = {}
    merged = 0
    for row in rows:
        if row.get('canonical_id') and row['canonical_id'] != row['id']:
            merged += 1
            continue
        category = row.get('category') or '待分类'
        categories[category] = categories.get(category, 0) + 1
    latest = db.fetchone("SELECT * FROM library_organization_runs WHERE state='applied' ORDER BY applied_at DESC,rowid DESC LIMIT 1")
    result = decode((latest or {}).get('plan_json'), {}).get('result')
    prior_count = db.fetchone("SELECT COUNT(*) AS n FROM papers WHERE id LIKE 'prior:%' AND COALESCE(retrieval_status,'')!='off_topic'")['n']
    if prior_count:
        categories['先验笔记'] = categories.get('先验笔记',0) + prior_count
    preview=incremental_preview(db,*snapshot(db),request.app.state.workspace_root)
    from src.knowledge.library_facets import facet_counts
    facets=facet_counts([r for r in rows if not r.get('canonical_id') or r['canonical_id']==r['id']])
    return {'records': len(rows), 'papers': len(rows)-merged, 'merged_records': merged,
            'prior_count':prior_count, 'library_items':len(rows)-merged+prior_count,
            'categories': [{'name': k, 'count': v} for k,v in sorted(categories.items())], 'facets':facets,
            'estimated_calls':preview['estimated_calls'],'pending_papers':preview['pending_papers'],
            'reused_papers':preview['reused_papers'],
            'max_papers': MAX_PAPERS, 'latest': result}


@router.post('/api/library/organize', status_code=202)
async def submit_organization(request: Request):
    info = await organization_info(request)
    if info['estimated_calls'] and not request.app.state.llm._ready:
        raise HTTPException(409, '请先在连接与设置中配置模型 API')
    if not info['records']:
        raise HTTPException(400, '论文库中没有可整理的论文')
    if info['records'] > MAX_PAPERS:
        raise HTTPException(400, f'单轮整理最多 {MAX_PAPERS} 条记录，本次没有处理或截断资料')
    key = request.headers.get('Idempotency-Key') or str(uuid.uuid4())
    if len(key) > 128:
        raise HTTPException(400, '幂等键过长')
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'library:'+key))
    payload = {'mode': 'library_organize', 'session_id': LIBRARY_SESSION, 'organization_id': run_id,
               'research_limits': {'timeout_minutes': 60},
               'max_model_calls': min(600, 6 * (1 + math.ceil(MAX_PAPERS/BATCH_SIZE)))}
    job = await call(request, 'submit', id=str(uuid.uuid4()), session_id=LIBRARY_SESSION,
                     request_key='library:'+key if len(key) <= 120 else key, payload=payload)
    return {'job_id': job['id'], 'run_id': run_id, 'status': job['status']}


@router.post('/api/library/organization/{run_id}/undo')
async def undo(run_id: str, request: Request):
    jobs = await call(request, 'list')
    if any(j['session_id'] == LIBRARY_SESSION and j['status'] in {'queued','running','cancel_requested'} for j in jobs):
        raise HTTPException(409, '请先取消或等待正在执行的整理任务')
    try:
        return undo_organization(request.app.state.db, run_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
