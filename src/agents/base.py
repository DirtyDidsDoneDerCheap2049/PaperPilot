from pydantic import BaseModel, ConfigDict, Field
from pathlib import Path


class AgentContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    workspace_id: str = ""
    session_id: str = ""
    workspace_root: Path = Path(".")
    config: dict = Field(default_factory=dict)


class AgentResult(BaseModel):
    status: str
    data: dict = Field(default_factory=dict)
    error: str | None = None


class BaseAgent:
    name: str = "base"

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        raise NotImplementedError
