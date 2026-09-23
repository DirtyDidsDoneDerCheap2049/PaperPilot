import json, logging, uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class EvidenceStore:
    def __init__(self, db, vector_store):
        self.db = db
        self.vs = vector_store

    def add_evidence(self, paper_id: str, source_type: str, section: str,
                     page: int | None, content: str, metadata: dict | None = None,
                     paper_title: str = "") -> str:
        chunk_id = f"{paper_id}_ev_{uuid.uuid4().hex[:8]}"
        self.db.execute(
            """INSERT OR REPLACE INTO evidence_chunks
               (id, paper_id, source_type, section, page, content, metadata_json)
               VALUES (?,?,?,?,?,?,?)""",
            (chunk_id, paper_id, source_type, section, page,
             content, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        # Vectorize
        from src.knowledge.vector_store import VectorDoc
        full_meta = {
            "paper_id": str(paper_id),
            "paper_title": str(paper_title or ""),
            "source_type": str(source_type),
            "section": str(section),
        }
        self.vs.add_documents("evidence_chunks", [
            VectorDoc(id=chunk_id, text=content[:3000], metadata=full_meta),
        ])
        return chunk_id

    def get_evidence_for_paper(self, paper_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM evidence_chunks WHERE paper_id=? ORDER BY section",
            (paper_id,),
        )
        return [dict(r) for r in rows]

    def get_evidence_for_report(self, paper_ids: list[str]) -> list[dict]:
        all_evidence = []
        for pid in paper_ids:
            all_evidence.extend(self.get_evidence_for_paper(pid))
        return all_evidence
