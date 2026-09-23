import json, asyncio, logging, uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class KBManager:
    def __init__(self, sqlite_store, vector_store):
        self.db = sqlite_store
        self.vs = vector_store
        self._lock = asyncio.Lock()

    async def upsert_paper(self, paper: dict) -> str:
        async with self._lock:
            paper_id = paper.get("id", str(uuid.uuid4()))
            existing = self.db.fetchone(
                "SELECT * FROM papers WHERE id=?", (paper_id,)
            )
            authors_json = json.dumps(
                paper.get("authors", []), ensure_ascii=False
            )
            incoming_status = paper.get("retrieval_status", "metadata_only")
            status = self._merged_status(existing, incoming_status)
            missing_reason = paper.get("missing_reason")
            if existing and missing_reason is None and status == existing.get("retrieval_status"):
                missing_reason = existing.get("missing_reason")
            if status in ("downloaded", "parsed"):
                missing_reason = None
            fulltext_path = (
                paper.get("fulltext_path") or paper.get("local_pdf_path") or
                (existing or {}).get("fulltext_path")
            )
            parsed_markdown_path = paper.get("parsed_markdown_path") or (existing or {}).get("parsed_markdown_path")
            parsed_json_path = paper.get("parsed_json_path") or (existing or {}).get("parsed_json_path")
            if existing:
                self.db.execute(
                    """UPDATE papers SET title=?, authors_json=?, year=?,
                       venue=?, doi=?, arxiv_id=?, semantic_scholar_id=?,
                       url=?, open_access_pdf_url=?, abstract=?,
                       citation_count=?, retrieval_status=?, missing_reason=?,
                       fulltext_path=?, parsed_markdown_path=?, parsed_json_path=?,
                       updated_at=datetime('now') WHERE id=?""",
                    (paper.get("title"), authors_json, paper.get("year"),
                     paper.get("venue"), paper.get("doi"),
                     paper.get("arxiv_id"), paper.get("semantic_scholar_id"),
                     paper.get("url"), paper.get("open_access_pdf_url"),
                     paper.get("abstract"), paper.get("citation_count", 0),
                     status, missing_reason, fulltext_path,
                     parsed_markdown_path, parsed_json_path, paper_id),
                )
            else:
                self.db.execute(
                    """INSERT INTO papers (id, title, authors_json, year,
                       venue, doi, arxiv_id, semantic_scholar_id, url,
                       open_access_pdf_url, abstract, citation_count,
                       retrieval_status, missing_reason, fulltext_path,
                       parsed_markdown_path, parsed_json_path)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (paper_id, paper.get("title"), authors_json,
                     paper.get("year"), paper.get("venue"),
                     paper.get("doi"), paper.get("arxiv_id"),
                     paper.get("semantic_scholar_id"), paper.get("url"),
                     paper.get("open_access_pdf_url"), paper.get("abstract"),
                     paper.get("citation_count", 0),
                     status, missing_reason, fulltext_path,
                     parsed_markdown_path, parsed_json_path),
                )
            text = f"{paper.get('title','')}\n{paper.get('abstract','')}"
            from src.knowledge.vector_store import VectorDoc
            self.vs.add_documents("papers", [
                VectorDoc(id=paper_id, text=text[:2000],
                          metadata={"has_fulltext": str(status in ("downloaded", "parsed"))})
            ])
            # Also add to notes collection
            note_text = f"paper: {paper.get('title','')}\nvenue: {paper.get('venue','')}\nyear: {paper.get('year','')}\n{paper.get('abstract','')}"
            self.vs.add_documents("notes", [
                VectorDoc(id=paper_id, text=note_text[:2000],
                          metadata={"venue": str(paper.get("venue") or ""), "year": str(paper.get("year") or "")})
            ])
            return paper_id

    def _merged_status(self, existing: dict | None, incoming: str | None) -> str:
        incoming = incoming or "metadata_only"
        if not existing:
            return incoming
        current = existing.get("retrieval_status") or "metadata_only"
        low_confidence = {"metadata_only", "missing_fulltext", "parse_failed", "failed"}
        if existing.get("parsed_markdown_path") and incoming in low_confidence:
            return "parsed"
        if existing.get("fulltext_path") and current in low_confidence and incoming in low_confidence:
            return "downloaded"
        if current in {"off_topic", "parsed", "downloaded"} and incoming in low_confidence:
            return current
        if current == "parsed" and incoming == "downloaded":
            return current
        return incoming

    async def save_innovation_profile(self, paper_id: str, profile: dict):
        async with self._lock:
            if not isinstance(profile, dict):
                profile = {"paper_id": paper_id, "innovation_detail": str(profile), "confidence": 0.0}
            evidence_items = profile.get("evidence") or []
            if not isinstance(evidence_items, list):
                evidence_items = [evidence_items]
            profile_json = json.dumps(profile, ensure_ascii=False)
            evidence_json = json.dumps(evidence_items, ensure_ascii=False)
            existing = self.db.fetchone(
                "SELECT paper_id FROM innovation_profiles WHERE paper_id=?",
                (paper_id,)
            )
            if existing:
                self.db.execute(
                    """UPDATE innovation_profiles SET profile_json=?,
                       extraction_model=?, evidence_json=?, confidence=?
                       WHERE paper_id=?""",
                    (profile_json, profile.get('extraction_model'), evidence_json,
                     profile.get("confidence", 0.5), paper_id),
                )
            else:
                self.db.execute(
                    """INSERT INTO innovation_profiles
                       (paper_id, profile_json, extraction_model,
                        evidence_json, confidence)
                       VALUES (?,?,?,?,?)""",
                    (paper_id, profile_json, profile.get('extraction_model'),
                     evidence_json, profile.get("confidence", 0.5)),
                )

            # Write evidence_chunks
            for i, ev in enumerate(evidence_items):
                if not isinstance(ev, dict):
                    ev = {"quote": str(ev), "supports": ""}
                chunk_id = f"{paper_id}_ev_{i}"
                self.db.execute(
                    """INSERT OR REPLACE INTO evidence_chunks
                       (id, paper_id, source_type, section, page, content, metadata_json)
                       VALUES (?,?,?,?,?,?,?)""",
                    (chunk_id, paper_id,
                     "fulltext" if profile.get("has_fulltext") else "abstract",
                     ev.get("section", ""), ev.get("page"),
                     ev.get("quote", ev.get("supports", "")),
                     json.dumps(ev, ensure_ascii=False)),
                )

            profile_text = " ".join(str(profile.get(key) or "") for key in (
                "research_domain", "task_or_problem", "method_summary",
                "method_category", "method_subcategory", "method_component",
                "innovation_detail", "limitations",
            ))
            from src.knowledge.vector_store import VectorDoc
            self.vs.add_documents("innovation_profiles", [
                VectorDoc(id=paper_id, text=profile_text[:3000],
                          metadata={
                              "domain": str(profile.get("research_domain") or ""),
                              "task": str(profile.get("task_or_problem") or ""),
                              "stage": str(profile.get("method_component") or ""),
                              "method": str(profile.get("method_subcategory") or ""),
                              "confidence": str(profile.get("confidence") or 0.5),
                          })
            ])

    async def save_parsed_paper(self, paper_id: str, markdown_path: Path,
                                json_path: Path | None = None):
        async with self._lock:
            self.db.execute(
                """UPDATE papers SET parsed_markdown_path=?,
                   parsed_json_path=?, retrieval_status='parsed',
                   updated_at=datetime('now') WHERE id=?""",
                (str(markdown_path), str(json_path) if json_path else None,
                 paper_id),
            )

    async def search_related(self, query: str,
                             top_k: int = 20) -> list[dict]:
        paper_hits = self.vs.query("papers", query, top_k)
        profile_hits = self.vs.query("innovation_profiles", query, top_k)
        hits = paper_hits + profile_hits
        results = []
        seen = set()
        for h in hits:
            if h.id in seen:
                continue
            seen.add(h.id)
            paper = self.db.fetchone(
                """SELECT id, title, abstract, year, venue, doi, arxiv_id,
                          semantic_scholar_id, url, open_access_pdf_url,
                          retrieval_status, missing_reason, fulltext_path,
                          parsed_markdown_path
                   FROM papers WHERE id=?""", (h.id,)
            )
            profile = self.db.fetchone(
                "SELECT profile_json FROM innovation_profiles WHERE paper_id=?",
                (h.id,)
            )
            results.append({
                "paper": dict(paper) if paper else {},
                "profile": json.loads(profile["profile_json"]) if profile else {},
                "distance": h.distance,
            })
        return results[:top_k]

    def get_evidence_for_paper(self, paper_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM evidence_chunks WHERE paper_id=?",
            (paper_id,),
        )
        return [dict(r) for r in rows]
