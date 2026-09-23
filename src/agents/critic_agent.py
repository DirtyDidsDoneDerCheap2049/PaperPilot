from src.agents.base import BaseAgent, AgentContext, AgentResult


class CriticAgent(BaseAgent):
    name = "CriticAgent"

    def __init__(self, llm=None):
        self.llm = llm

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        try:
            from src.analysis.critic import Critic
            c = Critic(self.llm)
            result = await c.critique(
                input_data.get("analysis", {}),
                input_data.get("innovations", []),
            )
            return AgentResult(status="completed", data=result)
        except Exception as e:
            return AgentResult(status="failed", error=str(e))
