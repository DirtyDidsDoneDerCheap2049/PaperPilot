from src.agents.base import BaseAgent, AgentContext, AgentResult


class AnalysisAgent(BaseAgent):
    name = "AnalyzeAgent"

    def __init__(self, llm=None):
        self.llm = llm

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        try:
            from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
            direction = input_data.get("direction", {})
            innovations = input_data.get("innovations", [])
            if not innovations:
                return AgentResult(status="completed", data={
                    "gaps": [], "matrix": {},
                    "message": "No innovations to analyze",
                })
            analysis = await EvidenceGroundedAnalyzer(self.llm).analyze(
                direction, innovations
            )
            return AgentResult(status="completed", data=analysis)
        except Exception as e:
            return AgentResult(status="failed", error=str(e))
