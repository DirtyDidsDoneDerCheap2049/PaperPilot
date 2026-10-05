import asyncio
import json
import logging
import os
import sys
import threading
import time
import contextlib
from pathlib import Path
from types import SimpleNamespace

OUTPUT = sys.stdout


def emit(value):
    OUTPUT.write(json.dumps(value, ensure_ascii=False)+'\n')
    OUTPUT.flush()


class Events:
    def __init__(self):
        self.agent = 'Orchestrator'
        self.item_label = ''

    async def broadcast(self, session_id, event):
        if event.get('type') == 'agent_started':
            self.agent = event.get('agent') or self.agent
            self.item_label = ''
        if event.get('type') == 'agent_progress':
            self.item_label = event.get('message') or self.item_label
        emit({'kind':'event','event':dict(event,session_id=session_id)})


async def run(workspace, payload):
    from src.app.factory import build_app
    app, config = build_app(workspace)
    state = app.state
    if payload.get('mode') == 'library_index':
        from src.knowledge.paper_retrieval import index_library
        events=Events()
        async def progress(title,completed,total):
            await events.broadcast('__paper_index__',{'type':'index_progress','message':title,'completed':completed,'total':total})
        try:
            data=await index_library(state.db,state.retriever,progress)
            emit({'kind':'result','ok':True,'data':data})
        except Exception:
            logging.exception('Fulltext indexing failed')
            emit({'kind':'result','ok':False,'error':'全文索引未完成；已完成的索引保留，可重试增量更新'})
        finally:
            state.retriever.close()
            await state.llm.close()
            if hasattr(state.vs,'close'):state.vs.close()
            state.db.close()
        return
    if payload.get('mode') == 'library_organize':
        from src.knowledge.library_organizer import organize_library, LIBRARY_SESSION
        state.llm.max_calls = max(1, min(600, int(payload.get('max_model_calls', 120))))
        events = Events()
        async def progress(message, completed, total):
            await events.broadcast(LIBRARY_SESSION, {'type':'library_progress', 'message':message,
                'completed':completed, 'total':total, 'model_calls':state.llm.calls})
        async def activity(data):
            # Do not store or render raw model output/reasoning for classification.
            if data.get('phase') != 'delta':
                await events.broadcast(LIBRARY_SESSION, {'type':'library_model_activity',
                    'phase':data.get('phase'), 'model_calls':state.llm.calls})
        state.llm.activity_callback = activity
        try:
            data = await organize_library(state.db, state.llm, workspace, payload['organization_id'], progress)
            data['usage_summary'] = state.llm.usage_summary()
            emit({'kind':'result','ok':True,'data':data,
                  'event':{'type':'library_organized','session_id':LIBRARY_SESSION,**data}})
        except Exception as exc:
            logging.exception('Library organization failed')
            # Field-validation errors are safe to display; provider errors may contain request data.
            error = str(exc) if isinstance(exc, ValueError) else '论文库整理未完成，请检查模型连接与余额后重试；已有分类断点保留，未应用不完整结果'
            emit({'kind':'result','ok':False,'error':error,'data':{'usage_summary':state.llm.usage_summary()}})
        finally:
            state.retriever.close()
            await state.llm.close()
            if hasattr(state.vs, 'close'):
                state.vs.close()
            state.db.close()
        return
    from src.runtime.research_limits import research_limits
    limits=research_limits({'research':payload.get('research_limits',config.get('research',{}))})
    state.llm.max_calls=limits['max_model_calls']
    state.llm.parallel_papers=limits['parallel_papers']
    config['research'] = {**config.get('research', {}), **limits}
    sid, mid = payload['session_id'], payload['message_id']
    events = Events()
    started = time.monotonic()
    async def activity(data):
        await events.broadcast(sid, {'type':'model_delta' if data.get('phase')=='delta' else 'model_activity',
                                    'agent':events.agent, 'item_label':events.item_label,
                                    'call':state.llm.calls, **data})
    async def heartbeat():
        while True:
            await events.broadcast(sid, {'type':'worker_heartbeat', 'agent':events.agent,
                                        'elapsed_seconds':round(time.monotonic()-started),
                                        'model_calls':state.llm.calls})
            await asyncio.sleep(5)
    # Structured research calls stream internally; validated stage results remain authoritative.
    if payload.get('mode') != 'report_chat':
        state.llm.activity_callback = activity
    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        if payload.get('mode')=='report_chat':
            import src.server.websocket as ws
            ws.manager = events
            from src.server.routes_chat import ChatRequest, _run_report_chat
            await _run_report_chat(ChatRequest(**payload), SimpleNamespace(app=app), sid, mid)
            emit({'kind':'result','ok':True,'data':{'usage_summary':state.llm.usage_summary()}})
            return
        from src.agents.base import AgentContext
        state.orchestrator.ws_manager=events
        result = await state.orchestrator.run(AgentContext(workspace_id=state.workspace_id,session_id=sid,workspace_root=workspace,config=config), payload)
        if result.status!='completed':
            emit({'kind':'result','ok':False,'error':result.error or '分析失败',
                  'data':{'usage_summary':state.llm.usage_summary()}})
            return
        data=result.data
        data['usage_summary'] = state.llm.usage_summary()
        event={'type':'report_ready','session_id':sid,'path':data.get('report_path_rel'),'content':data.get('report_content'),'report_path':data.get('report_path_rel'),'missing':data.get('missing_papers',[]),'evidence':data.get('evidence',[])}
        for key in ('papers_found','innovations_extracted','missing_count','gaps_count','provisional_gaps_count','assistant_message_id','user_message_id','report_summary','unprocessed_count','task_type','answer_status','benchmark_verified_count','answer_count','time_scope','mode','revision_only','source_report_path','new_papers_read','new_searches'):
            event[key]=data.get(key)
        emit({'kind':'result','ok':True,'data':data,'event':event})
    except Exception:
        logging.exception('Analysis failed')
        emit({'kind':'result','ok':False,'error':'分析失败，请查看设置或工作区诊断日志',
              'data':{'usage_summary':state.llm.usage_summary()}})
    finally:
        state.retriever.close()
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        await state.llm.close()
        if hasattr(state.vs, 'close'):
            state.vs.close()
        state.db.close()


def main(workspace=None):
    for stream in (sys.stdin, OUTPUT, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='strict')
    # Keep dependency prints away from the machine-readable pipe.
    sys.stdout=sys.stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        payload=json.loads(sys.stdin.readline())
        # Blocking CRT reads can hold handle locks used by native DLL imports.
        # Windows liveness checks therefore peek, rather than read the pipe.
        watch_fd=os.dup(sys.stdin.fileno())
        if os.name=='nt':
            import ctypes,msvcrt
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.PeekNamedPipe.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,
                ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
            kernel.PeekNamedPipe.restype=wintypes.BOOL
            watch_handle=msvcrt.get_osfhandle(watch_fd)
        def parent_watch():
            # The coordinator retains this pipe after sending the payload.
            # EOF means it exited; stop before a replacement worker can run.
            try:
                if os.name=='nt':
                    while kernel.PeekNamedPipe(watch_handle,None,0,None,None,None):
                        time.sleep(.1)
                else:
                    while os.read(watch_fd,4096):pass
            finally:
                os.close(watch_fd)
                os._exit(3)
        threading.Thread(target=parent_watch, daemon=True).start()
        asyncio.run(run(Path(workspace or sys.argv[1]).resolve(),payload))
    except Exception:
        logging.exception('Analysis failed')
        emit({'kind':'result','ok':False,'error':'分析失败，请查看设置或工作区诊断日志'})
        return 1
    return 0

if __name__=='__main__':
    raise SystemExit(main())
