"""Replay and live batching keep the same durable cursor and legacy contract."""
import asyncio
import json
from types import SimpleNamespace

from src.server.routes_jobs import stream_events


def test_batched_stream_replays_every_event_and_keeps_resume_cursor():
    async def run(batch, after):
        rows = [{'seq': i, 'body': '{}'} for i in range(1, 136)]
        async def rpc(op, **args):
            if op == 'get':
                return {'id': 'fixture', 'status': 'succeeded', 'payload': '{}', 'result': '{}'}
            return [row for row in rows if row['seq'] > args['after']][:200]
        async def disconnected():
            return False
        request = SimpleNamespace(headers={'last-event-id': str(after)}, is_disconnected=disconnected,
                                  app=SimpleNamespace(state=SimpleNamespace(tasks=SimpleNamespace(rpc=rpc))))
        response = await stream_events('fixture', request, batch=batch)
        frames = [frame async for frame in response.body_iterator]
        events, cursors = [], []
        for frame in frames:
            data = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith('data: ')))
            if data['kind'] == 'status':
                continue
            events.extend(data['rows'] if batch > 1 else [data['row']])
            cursors.append(int(frame.splitlines()[0][4:]))
        assert [row['seq'] for row in events] == list(range(after + 1, 136))
        assert cursors[-1] == 135
        assert len(cursors) == (135 - after + batch - 1) // batch
    for batch in (1, 64):
        asyncio.run(run(batch, 32))
