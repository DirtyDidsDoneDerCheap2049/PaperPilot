import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class VectorDoc(BaseModel):
    id: str
    text: str
    metadata: dict = Field(default_factory=dict)


class VectorHit(BaseModel):
    id: str
    text: str
    metadata: dict
    distance: float


class VectorStore:
    def __init__(self, persist_dir: Path, embedding_fn=None):
        try:
            import chromadb
        except ImportError:
            raise RuntimeError('语义检索需要安装 requirements-vector.txt；可先选择本地文本检索')
        persist_dir = Path(persist_dir)
        persist_dir.mkdir(parents=True, exist_ok=True)
        if embedding_fn:
            self.client = chromadb.PersistentClient(
                path=str(persist_dir),
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
        else:
            self.client = chromadb.PersistentClient(path=str(persist_dir))
        self._ef = embedding_fn

    def _get_or_create(self, name: str):
        if hasattr(self.client, "get_or_create_collection"):
            return self.client.get_or_create_collection(
                name, embedding_function=self._ef
            )
        try:
            return self.client.get_collection(name, embedding_function=self._ef)
        except Exception:
            return self.client.create_collection(name, embedding_function=self._ef)

    def add_documents(self, collection: str, docs: list[VectorDoc]):
        if not docs:
            return
        col = self._get_or_create(collection)
        payload = {
            "ids": [d.id for d in docs],
            "documents": [d.text for d in docs],
            "metadatas": [d.metadata for d in docs],
        }
        if hasattr(col, "upsert"):
            col.upsert(**payload)
            return

        # Compatibility path for older Chroma clients without Collection.upsert.
        try:
            col.delete(ids=payload["ids"])
        except Exception as exc:
            logger.debug("Legacy Chroma delete before add failed: %s", exc)
        col.add(**payload)

    def query(self, collection: str, query: str,
              top_k: int = 10, where: dict | None = None) -> list[VectorHit]:
        try:
            col = self._get_or_create(collection)
            kwargs = {"query_texts": [query], "n_results": top_k}
            if where:
                kwargs["where"] = where
            results = col.query(**kwargs)
            hits = []
            ids = results.get("ids", [[]])[0]
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]
            for cid, ctext, cmeta, cdist in zip(ids, docs, metas, dists):
                hits.append(VectorHit(
                    id=cid, text=ctext,
                    metadata=cmeta or {}, distance=cdist,
                ))
            return hits
        except Exception as e:
            logger.warning(f"Vector query failed for {collection}: {e}")
            return []


class LocalSearchStore:
    """Offline lexical index. Distances are ranks, not semantic similarity."""
    mode = '本地文本检索（无需 API，不提供语义相似度）'

    def __init__(self, path):
        import sqlite3
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(self.path,check_same_thread=False,timeout=5)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(id UNINDEXED, collection UNINDEXED, text, metadata UNINDEXED)')
        self.conn.commit()

    def add_documents(self,collection,docs):
        import json
        with self.conn:
            for d in docs:
                self.conn.execute('DELETE FROM documents WHERE id=? AND collection=?',(d.id,collection))
                self.conn.execute('INSERT INTO documents VALUES(?,?,?,?)',(d.id,collection,d.text,json.dumps(d.metadata,ensure_ascii=False)))

    def query(self,collection,query,top_k=10,where=None):
        import re,json
        terms=re.findall(r'\w+',query)[:30]
        if not terms: return []
        match=' OR '.join('"'+t+'"' for t in terms)
        rows=self.conn.execute('SELECT id,text,metadata,rank FROM documents WHERE documents MATCH ? AND collection=? ORDER BY rank LIMIT ?', (match,collection,max(1,top_k)*5)).fetchall()
        hits=[]
        for id,text,metadata,rank in rows:
            meta=json.loads(metadata)
            if where and any(meta.get(k)!=v for k,v in where.items()): continue
            hits.append(VectorHit(id=id,text=text,metadata=meta,distance=float(rank)))
        return hits[:top_k]

    def close(self):
        self.conn.close()
