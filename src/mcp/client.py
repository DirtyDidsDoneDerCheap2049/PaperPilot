import logging, json, asyncio
import httpx

logger = logging.getLogger(__name__)


class MCPClient:
    def __init__(self, servers_config: list[dict] | None = None):
        self.servers = servers_config or []
        self.transports: dict[str, object] = {}
        self.available_tools: dict[str, dict] = {}

    async def connect(self):
        results = []
        for srv in self.servers:
            transport = srv.get("transport", "stdio")
            name = srv.get("name", "unknown")
            try:
                if transport == "stdio":
                    from src.mcp.transports import StdioTransport
                    t = StdioTransport(
                        command=srv.get("command", ""),
                        args=srv.get("args", []),
                        env=srv.get("env", {}),
                    )
                    await t.start()
                    tools = await t.list_tools()
                elif transport == "http":
                    from src.mcp.transports import HTTPTransport
                    t = HTTPTransport(
                        url=srv.get("url", ""),
                        headers=srv.get("headers", {}),
                    )
                    tools = await t.list_tools()
                elif transport == "sse":
                    from src.mcp.transports import SSETransport
                    t = SSETransport(
                        url=srv.get("url", ""),
                        headers=srv.get("headers", {}),
                    )
                    tools = await t.list_tools()
                else:
                    logger.warning(f"Unknown MCP transport: {transport}")
                    continue

                self.transports[name] = t
                for tool in tools:
                    tname = f"mcp__{name}__{tool.get('name', '')}"
                    self.available_tools[tname] = tool
                logger.info(f"MCP {name}: {len(tools)} tools via {transport}")
                results.append({"name": name, "tools": len(tools), "status": "connected"})
            except Exception as e:
                logger.warning(f"MCP connect failed for {name}: {e}")
                results.append({"name": name, "status": "failed", "error": str(e)})
        return results

    async def call_tool(self, tool_name: str, args: dict) -> dict:
        parts = tool_name.split("__", 2)
        if len(parts) < 3:
            return {"error": f"Invalid MCP tool name: {tool_name}"}
        srv_name = parts[1]
        transport = self.transports.get(srv_name)
        if not transport:
            return {"error": f"MCP server not connected: {srv_name}"}
        try:
            return await transport.call_tool(parts[2], args)
        except Exception as e:
            return {"error": str(e)}

    async def search(self, query: str) -> list[dict]:
        results = []
        for tname, tool in self.available_tools.items():
            if "search" in tname.lower():
                try:
                    resp = await self.call_tool(tname, {"query": query})
                    if "results" in resp:
                        results.extend(resp["results"])
                except Exception:
                    pass
        return results

    async def stop(self):
        for t in self.transports.values():
            if hasattr(t, "stop"):
                try:
                    await t.stop()
                except Exception:
                    pass
