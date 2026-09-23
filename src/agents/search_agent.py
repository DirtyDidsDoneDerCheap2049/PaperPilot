from src.agents.base import BaseAgent, AgentContext, AgentResult


class SearchAgent(BaseAgent):
    name = "SearchAgent"

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        from src.tools.search_tools import search_all, merge_and_deduplicate
        queries = input_data.get("search_queries",
                                 [input_data.get("direction", "")])
        all_papers = []
        for q in queries[:5]:
            results = await search_all(q, 20)
            all_papers.extend(results)
        merged = await merge_and_deduplicate(all_papers)
        return AgentResult(status="completed", data={
            "papers": [m.model_dump() for m in merged],
        })


class KBWriteAgent(BaseAgent):
    name = "KBWriteAgent"

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        return AgentResult(status="completed", data={"saved": 0})


class AnalysisAgent(BaseAgent):
    name = "AnalyzeAgent"

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        return AgentResult(status="completed", data={
            "gaps": [], "matrix": {},
        })


class CriticAgent(BaseAgent):
    name = "CriticAgent"

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        return AgentResult(status="completed", data={
            "warnings": [], "suggestions": [],
        })


class MonitorAgent(BaseAgent):
    name = "MonitorAgent"

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        missing = input_data.get("missing_papers", [])
        return AgentResult(status="completed", data={
            "missing_count": len(missing), "papers": missing,
        })
