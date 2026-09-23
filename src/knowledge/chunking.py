"""Chatbox-style document chunking for knowledge base."""

import re
from pathlib import Path


def chunk_text(text: str, chunk_size: int = 800, chunk_overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    chunks = []
    start = 0
    paragraphs = text.split("\n\n")
    current = ""
    for p in paragraphs:
        if len(current) + len(p) < chunk_size:
            current += p + "\n\n"
        else:
            if current.strip():
                chunks.append(current.strip())
            current = p + "\n\n"
    if current.strip():
        chunks.append(current.strip())

    # Apply overlap
    overlapped = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            prev_end = chunks[i-1][-chunk_overlap:]
            chunk = prev_end + "\n...\n" + chunk
        overlapped.append(chunk[:chunk_size * 2])
    return overlapped


def chunk_markdown(md_text: str, chunk_size: int = 800) -> list[dict]:
    """Split markdown into sections, return [{title, content, section}...]."""
    sections = re.split(r"\n(#{1,3}\s)", md_text)
    chunks = []
    current_title = ""
    current_section = "introduction"

    for part in sections:
        if re.match(r"#{1,3}\s", part):
            current_title = part.strip()
            if "##" in part:
                current_section = part.replace("#", "").strip().lower()[:50]
        elif part.strip():
            sub = chunk_text(part.strip(), chunk_size)
            for s in sub:
                chunks.append({
                    "title": current_title,
                    "content": s,
                    "section": current_section,
                })
    return chunks


def chunk_pdf_markdown(md_path: Path) -> list[dict]:
    """Read a parsed markdown file and chunk it."""
    text = md_path.read_text(encoding="utf-8")
    return chunk_markdown(text)


class ChunkStore:
    """Chatbox-style chunk store: parent + child chunks."""

    def __init__(self, db, vector_store):
        self.db = db
        self.vs = vector_store

    def add_document(self, doc_id: str, chunks: list[dict],
                     metadata: dict | None = None):
        meta = metadata or {}
        from src.knowledge.vector_store import VectorDoc
        docs = []
        for i, chunk in enumerate(chunks):
            cid = f"{doc_id}_{i}"
            docs.append(VectorDoc(
                id=cid,
                text=chunk.get("content", "")[:2000],
                metadata={
                    "doc_id": doc_id,
                    "section": chunk.get("section", ""),
                    "title": chunk.get("title", ""),
                    **meta,
                },
            ))
        self.vs.add_documents("knowledge_chunks", docs)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        hits = self.vs.query("knowledge_chunks", query, top_k)
        return [{"id": h.id, "text": h.text, "metadata": h.metadata,
                 "distance": h.distance} for h in hits]

    def get_parent_context(self, doc_id: str) -> list[str]:
        hits = self.vs.query("knowledge_chunks", doc_id, 20)
        return [h.text for h in hits if h.metadata.get("doc_id") == doc_id]
