"""Initialize ToolRegistry with built-in tools (Chatbox-style)."""

from src.tools.registry import ToolRegistry, ToolSpec
from src.tools import builtin_tools


def register_all_tools(registry: ToolRegistry, kb_manager=None):
    registry.register(
        builtin_tools.arxiv_search_tool,
        ToolSpec(name="arxiv_search", description="Search arXiv for papers by keyword",
                 permission="external_api"),
    )
    registry.register(
        builtin_tools.fetch_url_tool,
        ToolSpec(name="fetch_url", description="Fetch content from a URL",
                 permission="external_api"),
    )
    registry.register(
        builtin_tools.sequential_thinking_tool,
        ToolSpec(name="sequential_thinking", description="Break down complex questions step by step",
                 permission="allow"),
    )
    if kb_manager:
        registry.register(
            lambda query="", top_k=5: builtin_tools.local_kb_search_tool(
                query, top_k, kb_manager),
            ToolSpec(name="kb_search", description="Search local knowledge base for related papers",
                     permission="allow"),
        )
    return registry
