"""Session Attachment RAG — Chatbox-style session-scoped knowledge base."""

import uuid, logging
from pathlib import Path
from src.knowledge.chunking import chunk_markdown, chunk_text

logger = logging.getLogger(__name__)


class SessionRAG:
    """Per-session attachment knowledge base for uploaded/large files."""

    def __init__(self, db, vector_store):
        self.db = db
        self.vs = vector_store

    def ensure_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS session_attachments (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                file_name TEXT,
                file_path TEXT,
                status TEXT DEFAULT 'pending',
                chunk_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

    async def index_file(self, session_id: str, file_path: Path,
                         file_name: str = "") -> str:
        attach_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO session_attachments (id, session_id, file_name, file_path, status) VALUES (?,?,?,?,?)",
            (attach_id, session_id, file_name, str(file_path), "indexing"),
        )
        try:
            text = file_path.read_text(encoding="utf-8")
            if file_path.suffix == ".md":
                chunks = chunk_markdown(text)
            else:
                chunks = [{"title": "", "content": c, "section": "text"}
                          for c in chunk_text(text)]

            from src.knowledge.vector_store import VectorDoc
            docs = []
            for i, chunk in enumerate(chunks):
                docs.append(VectorDoc(
                    id=f"sa_{attach_id}_{i}",
                    text=chunk.get("content", "")[:2000],
                    metadata={
                        "attach_id": attach_id,
                        "session_id": session_id,
                        "file_name": file_name,
                        "section": chunk.get("section", ""),
                    },
                ))
            self.vs.add_documents("session_attachments", docs)
            self.db.execute(
                "UPDATE session_attachments SET status='ready', chunk_count=? WHERE id=?",
                (len(docs), attach_id),
            )
            logger.info(f"SessionRAG indexed {file_name}: {len(docs)} chunks")
        except Exception as e:
            self.db.execute(
                "UPDATE session_attachments SET status='failed' WHERE id=?",
                (attach_id,),
            )
            raise
        return attach_id

    async def index_text(self, session_id: str, text: str,
                         name: str = "inline") -> str:
        attach_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO session_attachments (id, session_id, file_name, file_path, status) VALUES (?,?,?,?,?)",
            (attach_id, session_id, name, "", "indexing"),
        )
        try:
            chunks = chunk_text(text)
            from src.knowledge.vector_store import VectorDoc
            docs = []
            for i, chunk in enumerate(chunks):
                docs.append(VectorDoc(
                    id=f"sa_{attach_id}_{i}",
                    text=chunk[:2000],
                    metadata={"attach_id": attach_id, "session_id": session_id, "file_name": name},
                ))
            self.vs.add_documents("session_attachments", docs)
            self.db.execute(
                "UPDATE session_attachments SET status='ready', chunk_count=? WHERE id=?",
                (len(docs), attach_id),
            )
        except Exception:
            self.db.execute(
                "UPDATE session_attachments SET status='failed' WHERE id=?",
                (attach_id,),
            )
            raise
        return attach_id

    def search(self, session_id: str, query: str, top_k: int = 10) -> list[dict]:
        hits = self.vs.query("session_attachments", query, top_k,
                             where={"session_id": session_id})
        return [{"id": h.id, "text": h.text, "metadata": h.metadata,
                 "distance": h.distance} for h in hits]

    def list_attachments(self, session_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM session_attachments WHERE session_id=? ORDER BY created_at",
            (session_id,),
        )
        return [dict(r) for r in rows]

    def remove_session(self, session_id: str):
        attachments = self.list_attachments(session_id)
        for a in attachments:
            try:
                # Remove from vector store (delete by prefix)
                ids_to_delete = [f"sa_{a['id']}_{i}" for i in range(a.get("chunk_count", 0) * 2)]
                if ids_to_delete:
                    try:
                        col = self.vs.client.get_collection("session_attachments")
                        col.delete(ids=ids_to_delete[:1000])
                    except Exception:
                        pass
            except Exception:
                pass
        self.db.execute("DELETE FROM session_attachments WHERE session_id=?", (session_id,))
