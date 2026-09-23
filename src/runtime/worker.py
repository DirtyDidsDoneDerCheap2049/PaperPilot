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
    from src.runtime.research_limits import research_limits
    limits=research_limits({'research':payload.get('research_limits',config.get('research',{}))})
    state.llm.max_calls=limits['max_model_calls']
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
            emit({'kind':'result','ok':True,'data':{}})
            return
        from src.agents.base import AgentContext
        state.orchestrator.ws_manager=events
        result = await state.orchestrator.run(AgentContext(workspace_id=state.workspace_id,session_id=sid,workspace_root=workspace,config=config), payload)
        if result.status!='completed':
            emit({'kind':'result','ok':False,'error':result.error or '分析失败'})
            return
        data=result.data
        event={'type':'report_ready','session_id':sid,'path':data.get('report_path_rel'),'content':data.get('report_content'),'report_path':data.get('report_path_rel'),'missing':data.get('missing_papers',[]),'evidence':data.get('evidence',[])}
        for key in ('papers_found','innovations_extracted','missing_count','gaps_count','provisional_gaps_count','assistant_message_id','user_message_id','report_summary','unprocessed_count'):
            event[key]=data.get(key)
        emit({'kind':'result','ok':True,'data':data,'event':event})
    finally:
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
        def parent_watch():
            # The coordinator retains this pipe after sending the payload.
            # EOF means it exited; stop before a replacement worker can run.
            sys.stdin.read()
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
