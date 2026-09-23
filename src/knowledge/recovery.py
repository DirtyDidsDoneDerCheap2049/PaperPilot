"""Agent run recovery: persist intermediate workflow state for crash recovery."""

import json, logging
from datetime import datetime

logger = logging.getLogger(__name__)


class RecoveryManager:
    def __init__(self, db):
        self.db = db

    def save_checkpoint(self, session_id: str, step: str, data: dict):
        """Persist current step state."""
        self.db.execute(
            """INSERT INTO gap_analyses (id, session_id, direction, search_log_json)
               VALUES (?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
               direction=excluded.direction,
               search_log_json=excluded.search_log_json""",
            (f"checkpoint_{session_id}", session_id,
             data.get("direction", json.dumps(data)),
             json.dumps(data, ensure_ascii=False)),
        )

    def load_checkpoint(self, session_id: str) -> dict | None:
        row = self.db.fetchone(
            "SELECT search_log_json FROM gap_analyses WHERE id=?",
            (f"checkpoint_{session_id}",),
        )
        if row and row.get("search_log_json"):
            return json.loads(row["search_log_json"])
        return None

    def clear_checkpoint(self, session_id: str):
        self.db.execute(
            "DELETE FROM gap_analyses WHERE id=?",
            (f"checkpoint_{session_id}",),
        )

    def can_resume(self, session_id: str) -> bool:
        row = self.db.fetchone(
            "SELECT * FROM gap_analyses WHERE id=?",
            (f"checkpoint_{session_id}",),
        )
        return row is not None

    def get_pending_runs(self, session_id: str) -> list[dict]:
        rows = self.db.fetchall(
            """SELECT * FROM agent_runs WHERE session_id=?
               AND status IN ('running', 'pending')
               ORDER BY started_at""",
            (session_id,),
        )
        return [dict(r) for r in rows]

    def mark_abandoned_runs(self, session_id: str):
        self.db.execute(
            "UPDATE agent_runs SET status='abandoned', finished_at=datetime('now') WHERE session_id=? AND status IN ('running','pending')",
            (session_id,),
        )
