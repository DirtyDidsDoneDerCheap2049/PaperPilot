import logging

logger = logging.getLogger(__name__)


async def mcp_search(query: str, limit: int = 20) -> dict:
    """MCP search wrapper. Falls back gracefully."""
    try:
        from src.mcp.client import MCPClient
        client = MCPClient()
        results = await client.search(query)
        return {"results": results[:limit], "source": "mcp"}
    except Exception as e:
        logger.warning(f"MCP search unavailable: {e}")
        return {"results": [], "source": "mcp", "error": str(e)}
