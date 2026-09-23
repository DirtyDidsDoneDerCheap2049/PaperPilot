from src.agents.base import BaseAgent, AgentContext, AgentResult


class KBWriteAgent(BaseAgent):
    name = "KBWriteAgent"

    def __init__(self, kb_manager=None):
        self.kb = kb_manager

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        if not self.kb:
            return AgentResult(status="completed", data={"saved": 0, "error": "KBManager not available"})
        try:
            innovations = input_data.get("innovations", [])
            saved = 0
            for p in innovations:
                pid = p.get("paper_id")
                if pid:
                    await self.kb.save_innovation_profile(pid, p)
                    saved += 1
            return AgentResult(status="completed", data={"saved": saved})
        except Exception as e:
            return AgentResult(status="failed", error=str(e))
