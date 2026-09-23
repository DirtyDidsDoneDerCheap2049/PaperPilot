import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path


class TaskError(RuntimeError):
    pass


def native_binary() -> Path:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    name = 'reader_tasks.exe' if os.name == 'nt' else 'reader_tasks'
    return root / 'build' / name


class TaskService:
    """One native owner and at most one analysis process per desktop workspace."""
    def __init__(self, workspace: Path, db, broadcaster):
        self.workspace = Path(workspace)
        self.db = db
        self.broadcaster = broadcaster
        self.rpc_lock = asyncio.Lock()
        self.process = None
        self.worker = None
        self.loop_task = None
        self.stopping = False

    async def start(self):
        binary = native_binary()
        if not binary.is_file():
            raise TaskError('任务内核尚未构建，请运行 python scripts/build_native.py')
        self.process = await asyncio.create_subprocess_exec(
            str(binary), str(self.workspace / 'db/tasks.db'),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=8*1024*1024,
            env=dict(os.environ, READER_STORAGE=getattr(self.db,'backend','sqlite')),
            **({'creationflags': 0x08000000} if os.name == 'nt' else {}),
        )
        await self.rpc('ping')
        self.db.execute("UPDATE agent_runs SET status='interrupted', error='应用关闭，执行中断', finished_at=datetime('now') WHERE status='running'")
        self.db.execute("UPDATE sessions SET status='idle' WHERE status='busy'")
        self.loop_task = asyncio.create_task(self._loop())

    async def rpc(self, op, **args):
        async with self.rpc_lock:
            if not self.process or self.process.returncode is not None:
                raise TaskError('任务内核不可用，请重启应用')
            try:
                self.process.stdin.write((json.dumps(dict(op=op, **args), ensure_ascii=False)+'\n').encode())
                await self.process.stdin.drain()
                raw = await asyncio.wait_for(self.process.stdout.readline(), 15)
                if not raw:
                    raise TaskError('任务内核已关闭，工作区可能已被另一个窗口打开')
                reply = json.loads(raw)
            except (OSError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                self.process.kill()
                raise TaskError('任务内核通信失败，请重启应用后查询任务状态') from exc
            if not reply.get('ok'):
                raise TaskError(reply.get('error', 'native_error'))
            return reply['data']

    async def active_session(self, session_id):
        return next((j for j in await self.rpc('list') if j['session_id']==session_id and j['status'] in {'queued','running','cancel_requested'}), None)

    async def _loop(self):
        while not self.stopping:
            try:
                job = await self.rpc('claim')
                if job:
                    await self._execute(job)
                else:
                    await asyncio.sleep(.3)
            except asyncio.CancelledError:
                raise
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Task coordinator failed')
                if not self.process or self.process.returncode is not None:
                    return
                await asyncio.sleep(1)

    def worker_command(self):
        if getattr(sys, 'frozen', False):
            return [sys.executable, '--worker', str(self.workspace)]
        return [sys.executable, '-u', '-m', 'src.runtime.worker', str(self.workspace)]

    async def _execute(self, job):
        root = Path(__file__).resolve().parents[2]
        command = self.worker_command()
        logdir = self.workspace / '.agent_history'
        logdir.mkdir(exist_ok=True)
        logpath = logdir / 'worker.log'
        if logpath.exists() and logpath.stat().st_size > 5*1024*1024:
            logpath.replace(logdir / 'worker.previous.log')
        log = logpath.open('ab')
        result = None
        status, error = 'failed', '执行进程没有返回结果，请查看诊断日志'
        try:
            self.worker = await asyncio.create_subprocess_exec(
                *command, cwd=root, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=log, limit=8*1024*1024,
                env=dict(os.environ, READER_STORAGE=getattr(self.db,'backend','sqlite'), PYTHONIOENCODING='utf-8', PYTHONUTF8='1'),
                **({'creationflags': 0x08000000} if os.name == 'nt' else {}),
            )
            self.worker.stdin.write((job['payload']+'\n').encode())
            await self.worker.stdin.drain()
            # Keep stdin open as a parent-liveness channel until the worker exits.
            async def read_events():
                nonlocal result
                while raw := await self.worker.stdout.readline():
                    item = json.loads(raw)
                    if item.get('kind') == 'result':
                        result = item
                    elif item.get('kind') == 'event':
                        from datetime import datetime, timezone
                        event = dict(item['event'], job_id=job['id'], attempt_token=job['token'],
                                     emitted_at=datetime.now(timezone.utc).isoformat())
                        await self.rpc('event', id=job['id'], token=job['token'], body=event)
                        await self.broadcaster.broadcast(job['session_id'], event)
            reader = asyncio.create_task(read_events())
            from src.runtime.research_limits import research_limits
            limits=research_limits({'research':json.loads(job['payload']).get('research_limits',{})})
            deadline = asyncio.get_running_loop().time() + limits['timeout_minutes'] * 60
            try:
                while self.worker.returncode is None:
                    current = await self.rpc('get', id=job['id'])
                    if self.stopping or current['status']=='cancel_requested':
                        status = 'interrupted' if self.stopping else 'cancelled'
                        error = '应用关闭，执行中断' if self.stopping else '已取消。已发送的外部请求仍可能计费。'
                        self.worker.terminate()
                        break
                    if asyncio.get_running_loop().time()>deadline:
                        error=f"任务超过 {limits['timeout_minutes']} 分钟上限，已停止。已有资料保留，可调整研究预算后重新提交。"
                        self.worker.terminate()
                        break
                    if reader.done() and reader.exception():
                        raise reader.exception()
                    await asyncio.sleep(.2)
                await self.worker.wait()
                await reader
                if status not in {'cancelled','interrupted'} and result:
                    status = 'succeeded' if result.get('ok') else 'failed'
                    error = result.get('error','')
            finally:
                if self.worker.returncode is None:
                    self.worker.kill()
                    await self.worker.wait()
                if not reader.done():
                    reader.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await reader
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Worker failed')
            error='执行进程异常，详情见工作区诊断日志'
        finally:
            self.worker = None
            log.close()
        final = await self.rpc('finish', id=job['id'], token=job['token'], status=status, result=(result or {}).get('data',{}), error=error)
        with self.db.transaction():
            self.db.execute("UPDATE sessions SET status='idle' WHERE id=?",(job['session_id'],))
            self.db.execute("UPDATE agent_runs SET status=?,error=?,finished_at=datetime('now') WHERE session_id=? AND status='running'",(final['status'],error,job['session_id']))
        if final['status']=='succeeded' and result and result.get('event'):
            await self.broadcaster.broadcast(job['session_id'], result['event'])
        elif final['status']!='succeeded':
            await self.broadcaster.broadcast(job['session_id'], {'type':'agent_cancelled' if final['status']=='cancelled' else 'agent_failed','session_id':job['session_id'],'agent':'Orchestrator','error':error,'message':error})
        await self.broadcaster.broadcast(job['session_id'], {'type':'job_updated','session_id':job['session_id'],'job_id':job['id'],'status':final['status']})

    async def close(self):
        self.stopping=True
        if self.loop_task:
            try:
                await asyncio.wait_for(self.loop_task, 10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if self.worker and self.worker.returncode is None:
                    self.worker.kill()
                    await self.worker.wait()
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            await self.process.wait()
