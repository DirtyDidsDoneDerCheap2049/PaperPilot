from pydantic import BaseModel
from typing import Callable
from datetime import datetime
import inspect, json, logging

logger = logging.getLogger(__name__)


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    permission: str = "allow"


class ToolRegistry:
    def __init__(self, db=None):
        self._tools: dict[str, Callable] = {}
        self._specs: dict[str, ToolSpec] = {}
        self.db = db

    def register(self, func: Callable, spec: ToolSpec):
        self._tools[spec.name] = func
        self._specs[spec.name] = spec

    def list_tools(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def _permission_status(self, spec: ToolSpec) -> str:
        permission = spec.permission or "allow"
        if permission in ("allow", "allow_log", "ask", "deny", "ask_or_config_allow"):
            return permission
        from src.tools.permissions import check_permission
        return check_permission(permission)

    async def call(self, agent_run_id: str, name: str, args: dict) -> dict:
        spec = self._specs.get(name)
        if not spec:
            return {"error": f"Tool not found: {name}"}

        started = datetime.now().isoformat()
        call_id = name + "_" + started
        if self.db:
            self.db.execute(
                """INSERT INTO tool_calls (id, agent_run_id, tool_name, args_json, status, started_at)
                   VALUES (?,?,?,?,?,?)""",
                (call_id, agent_run_id, name,
                 json.dumps(args, ensure_ascii=False), "running", started),
            )

        permission = self._permission_status(spec)
        if permission in ("deny", "ask", "ask_or_config_allow"):
            result_dict = {
                "error": "permission_required" if permission != "deny" else "permission_denied",
                "tool": name,
                "permission": spec.permission,
                "policy": permission,
            }
            if self.db:
                self.db.execute(
                    """UPDATE tool_calls SET status='blocked', result_json=?,
                       finished_at=? WHERE id=?""",
                    (json.dumps(result_dict, ensure_ascii=False),
                     datetime.now().isoformat(), call_id),
                )
            return result_dict

        try:
            func = self._tools[name]
            result = func(**args)
            if inspect.isawaitable(result):
                result = await result
            result_dict = result if isinstance(result, dict) else {"value": str(result)}
            if self.db:
                self.db.execute(
                    """UPDATE tool_calls SET status='completed', result_json=?,
                       finished_at=? WHERE id=?""",
                    (json.dumps(result_dict, ensure_ascii=False),
                     datetime.now().isoformat(), call_id),
                )
            return result_dict
        except Exception as e:
            logger.error(f"Tool call {name} failed: {e}")
            if self.db:
                self.db.execute(
                    """UPDATE tool_calls SET status='failed', result_json=?,
                       finished_at=? WHERE id=?""",
                    (json.dumps({"error": str(e)}), datetime.now().isoformat(),
                     call_id),
                )
            return {"error": str(e)}
