import asyncio, json, logging, subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class StdioTransport:
    """Real stdio transport for MCP via subprocess."""

    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict | None = None):
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.process: subprocess.Popen | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict = {}
        self._next_id = 0

    async def start(self):
        import os
        full_env = {**os.environ, **self.env}
        self.process = subprocess.Popen(
            [self.command] + self.args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=full_env,
            text=False,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        # Initialize
        return await self._send_request("initialize", {"protocolVersion": "0.1"})

    async def _read_loop(self):
        try:
            while self.process and self.process.stdout:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self.process.stdout.readline
                )
                if not line:
                    break
                msg = json.loads(line.decode("utf-8"))
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    future.set_result(msg)
        except Exception as e:
            logger.warning(f"StdioTransport read error: {e}")

    async def _send_request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        msg_id = self._next_id
        msg = json.dumps({
            "jsonrpc": "2.0", "id": msg_id, "method": method, "params": params,
        })
        future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future
        if self.process and self.process.stdin:
            self.process.stdin.write((msg + "\n").encode("utf-8"))
            self.process.stdin.flush()
        try:
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise

    async def list_tools(self) -> list[dict]:
        resp = await self._send_request("tools/list", {})
        return resp.get("result", {}).get("tools", [])

    async def call_tool(self, tool_name: str, args: dict) -> dict:
        resp = await self._send_request("tools/call", {
            "name": tool_name, "arguments": args,
        })
        return resp.get("result", {})

    async def stop(self):
        if self._reader_task:
            self._reader_task.cancel()
        if self.process:
            self.process.terminate()
            self.process = None


class HTTPTransport:
    """Real HTTP transport for MCP servers."""

    def __init__(self, url: str, headers: dict | None = None):
        self.url = url.rstrip("/")
        self.headers = headers or {}

    async def list_tools(self) -> list[dict]:
        import httpx
        async with httpx.AsyncClient(timeout=10) as cli:
            resp = await cli.get(f"{self.url}/tools/list", headers=self.headers)
            if resp.status_code == 200:
                return resp.json().get("tools", [])
            return []

    async def call_tool(self, tool_name: str, args: dict) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=30) as cli:
            resp = await cli.post(
                f"{self.url}/tools/call",
                headers={**self.headers, "Content-Type": "application/json"},
                json={"name": tool_name, "arguments": args},
            )
            if resp.status_code == 200:
                return resp.json().get("result", {})
            return {"error": f"HTTP {resp.status_code}"}


class SSETransport:
    """SSE transport for MCP servers."""

    def __init__(self, url: str, headers: dict | None = None):
        self.url = url.rstrip("/")
        self.headers = headers or {}

    async def list_tools(self) -> list[dict]:
        import httpx
        async with httpx.AsyncClient(timeout=10) as cli:
            resp = await cli.get(f"{self.url}/sse", headers={
                **self.headers, "Accept": "text/event-stream",
            })
            return []  # SSE tools listing depends on server implementation

    async def call_tool(self, tool_name: str, args: dict) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=30) as cli:
            resp = await cli.post(
                f"{self.url}/tools/call",
                headers={**self.headers, "Content-Type": "application/json"},
                json={"name": tool_name, "arguments": args},
            )
            if resp.status_code == 200:
                return resp.json().get("result", {})
            return {"error": f"HTTP {resp.status_code}"}
