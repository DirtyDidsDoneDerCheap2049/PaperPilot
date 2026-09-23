from src.agents.base import BaseAgent, AgentContext, AgentResult


class MonitorAgent(BaseAgent):
    name = "MonitorAgent"

    def __init__(self, db=None):
        self.db = db

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        papers = input_data.get("papers", [])
        missing = [p for p in papers
                   if p.get("retrieval_status") in ("metadata_only", "missing_fulltext")]

        # Update paper tracker
        tracker_path = ctx.workspace_root / ".paper_tracker.json"
        try:
            import json
            existing = []
            if tracker_path.exists():
                existing = json.loads(tracker_path.read_text(encoding="utf-8"))
            for m in missing:
                existing.append({
                    "paper_id": m.get("id", ""),
                    "title": m.get("title", ""),
                    "source": m.get("source", ""),
                    "venue": m.get("venue", ""),
                    "year": m.get("year", ""),
                    "url": m.get("url", ""),
                })
            tracker_path.write_text(json.dumps(existing[-100:], ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        except Exception:
            pass

        if self.db:
            for m in missing:
                paper_id = m.get("id")
                if not paper_id:
                    continue
                try:
                    self.db.execute(
                        """UPDATE papers SET retrieval_status='missing_fulltext',
                           missing_reason=COALESCE(missing_reason, 'no_open_fulltext'),
                           updated_at=datetime('now')
                           WHERE id=? AND (retrieval_status IS NULL OR retrieval_status IN ('metadata_only', 'missing_fulltext'))""",
                        (paper_id,),
                    )
                    m["retrieval_status"] = "missing_fulltext"
                    m.setdefault("missing_reason", "no_open_fulltext")
                except Exception:
                    pass

        return AgentResult(status="completed", data={
            "missing_count": len(missing),
            "papers": missing,
            "suggestion": "请手动下载后放入 papers/manual/ 文件夹",
        })
