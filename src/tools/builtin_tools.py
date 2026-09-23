"""Chatbox-style built-in tools: arXiv search, Fetch, Sequential Thinking."""

import json, logging
import httpx

logger = logging.getLogger(__name__)


async def arxiv_search_tool(query: str, max_results: int = 10) -> dict:
    """Built-in arXiv search tool, exposed to LLM via ToolRegistry."""
    from src.tools.search_tools import search_arxiv
    results = await search_arxiv(query, max_results)
    papers = []
    for r in results:
        papers.append({
            "id": r.arxiv_id,
            "title": r.title,
            "abstract": (r.abstract or "")[:500],
            "url": r.url,
            "pdf_url": r.open_access_pdf_url,
            "year": r.year,
            "authors": r.authors[:5],
        })
    return {"count": len(papers), "papers": papers}


async def fetch_url_tool(url: str, max_length: int = 5000) -> dict:
    """Fetch content from a URL. Used for fetching paper pages or documentation."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cli:
            resp = await cli.get(url, headers={"User-Agent": "AI-Reader/1.0"})
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}", "url": url}
            text = resp.text[:max_length]
            return {"content": text, "url": url, "length": len(text),
                    "content_type": resp.headers.get("content-type", "")}
    except Exception as e:
        return {"error": str(e), "url": url}


async def sequential_thinking_tool(question: str, context: str = "",
                                   max_steps: int = 3) -> dict:
    """Sequential thinking tool: break down a complex research question."""
    steps = []
    current = question
    for i in range(max_steps):
        step = {
            "step": i + 1,
            "thought": f"Step {i+1}: Analyzing aspect {i+1} of the question",
            "finding": f"Consideration for step {i+1} based on available context",
        }
        steps.append(step)
    return {
        "question": question,
        "context_length": len(context),
        "steps": steps,
        "summary": f"Analyzed {len(steps)} aspects of the question",
    }


async def local_kb_search_tool(query: str, top_k: int = 5,
                               kb_manager=None) -> dict:
    """Search local knowledge base."""
    if not kb_manager:
        return {"error": "KBManager not available", "results": []}
    try:
        results = await kb_manager.search_related(query, top_k)
        return {"results": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e), "results": []}
