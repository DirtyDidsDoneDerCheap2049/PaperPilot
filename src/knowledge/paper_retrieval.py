"""Incremental full-paper indexing and scoped lexical/semantic retrieval.

Chroma stores real multilingual vectors; SQLite activates verified generations
and provides lexical ranking. Originals remain the source of truth.
"""
import hashlib
import re
import sqlite3
import threading
from pathlib import Path
from src.llm.local_embedding import LocalEmbedding

INDEX_VERSION = 'full-paper-v1'


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def paper_source(paper, workspace):
    root = Path(workspace).resolve()
    for field in ('parsed_markdown_path', 'fulltext_path'):
        value = paper.get(field)
        if not value:
            continue
        path = Path(value)
        path = path if path.is_absolute() else root / path
        path = path.resolve()
        if path.is_relative_to(root) and path.is_file() and path.suffix.lower() in {'.md', '.txt'}:
            return path, path.read_text('utf-8')
    pdf=paper.get('local_pdf_path') or paper.get('fulltext_path')
    if pdf:
        from src.parsing.pymupdf_parser import cached_table_view
        path = cached_table_view(Path(pdf), root)
        return path, path.read_text('utf-8')
    return None, None


def parent_spans(text):
    # Blank-line boundaries keep complete Markdown tables together. Long prose
    # paragraphs remain intact too; input budgeting happens at the reading layer.
    paragraphs = list(re.finditer(r'\S[\s\S]*?(?=\n\s*\n|\Z)', text))
    result = []
    start = end = 0
    heading = ''
    for match in paragraphs:
        block = match.group()
        new_heading = re.match(r'#{1,6}\s+(.+)', block)
        if end > start and (match.end() - start > 7000 or new_heading):
            result.append((start, end, heading))
            start = match.start()
        if new_heading:
            heading = new_heading.group(1).strip()
        if end == start:
            start = match.start()
        end = match.end()
    if end > start:
        result.append((start, end, heading))
    return result or [(0, len(text), '')]


def chunks(text, encoder):
    offsets = encoder.offsets(text)
    parents = parent_spans(text)
    for index in range(0, len(offsets), 88):
        window = offsets[index:index+112]
        if not window:
            break
        start, end = window[0][0], window[-1][1]
        parent = next((p for p in parents if p[0] <= start < p[1]), parents[-1])
        # A chunk may cross a paragraph boundary; include both parent blocks.
        last_parent = next((p for p in parents if p[0] <= end-1 < p[1]), parent)
        yield {'start': start, 'end': end, 'parent_start': parent[0], 'parent_end': last_parent[1],
               'section': parent[2], 'text': text[start:end]}


class PaperRetriever:
    def __init__(self, workspace, encoder=None):
        self.root = Path(workspace).resolve()
        self.encoder = encoder or LocalEmbedding()
        self.directory = self.root / 'db/paper-retrieval'
        if not self.directory.resolve().is_relative_to(self.root):
            raise ValueError('索引目录越出当前工作区')
        for candidate in (self.root/'db',self.directory):
            if candidate.is_symlink() or candidate.is_junction():
                raise ValueError('索引目录不能是链接')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'manifest.db', check_same_thread=False, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS documents(paper_id TEXT PRIMARY KEY, path TEXT NOT NULL,
                sha TEXT NOT NULL, fingerprint TEXT NOT NULL, chars INTEGER NOT NULL, title TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,paper_id TEXT NOT NULL,
                start INTEGER,end INTEGER,parent_start INTEGER,parent_end INTEGER,section TEXT,text TEXT);
            CREATE INDEX IF NOT EXISTS chunk_paper ON chunks(paper_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS lexical USING fts5(id UNINDEXED,text,tokenize='trigram');
        ''')
        self.db.commit()
        self._collection = None
        self._lock = threading.RLock()

    @property
    def fingerprint(self):
        return INDEX_VERSION + '-' + self.encoder.fingerprint

    def _vectors(self):
        if self._collection is None:
            import chromadb
            from chromadb.config import Settings
            client = chromadb.PersistentClient(path=str(self.directory / 'chroma'),
                settings=Settings(anonymized_telemetry=False))
            self._collection = client.get_or_create_collection('full_paper_chunks',
                embedding_function=None, metadata={'hnsw:space': 'cosine'})
        return self._collection

    def status(self):
        with self._lock:
            count = self.db.execute('SELECT COUNT(*) FROM documents WHERE fingerprint=?', (self.fingerprint,)).fetchone()[0]
            total = self.db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
            return {'mode': '本地文本＋向量' if self.encoder.available else '本地向量模型缺失',
                    'model_ready': self.encoder.available, 'model': self.encoder.name,
                    'model_fingerprint': self.encoder.fingerprint, 'documents': count, 'chunks': total,
                    'external_requests': 0, 'version': INDEX_VERSION}

    def is_current(self, paper_id, path, text):
        with self._lock:
            row=self.db.execute('SELECT sha,path,fingerprint FROM documents WHERE paper_id=?',(paper_id,)).fetchone()
            return bool(row and row['sha']==digest(text) and row['path']==str(Path(path).resolve()) and row['fingerprint']==self.fingerprint)

    def request_update(self):
        # The task coordinator schedules this persisted marker after five idle
        # seconds, so a cold embedding model does not delay first AI output.
        marker=self.directory/'needs-update'
        if marker.is_symlink() or not marker.resolve().is_relative_to(self.root):
            raise ValueError('索引更新标记不能是工作区外的链接')
        marker.touch()

    def index(self, paper, path, text):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError('索引源文件必须位于当前工作区')
        if path.read_text('utf-8') != text:
            raise ValueError('索引输入与源文件不一致')
        pid, sha = paper['id'], digest(text)
        from filelock import FileLock
        with self._lock, FileLock(str(self.directory / 'write.lock'), timeout=120):
            old = self.db.execute('SELECT * FROM documents WHERE paper_id=?', (pid,)).fetchone()
            if old and old['sha'] == sha and old['fingerprint'] == self.fingerprint and old['path'] == str(path):
                return {'paper_id': pid, 'status': 'reused', 'chunks': 0}
            parts = list(chunks(text, self.encoder))
            if not parts:
                return {'paper_id': pid, 'status': 'empty', 'chunks': 0}
            generation = digest(pid + sha + self.fingerprint)
            ids = [generation + ':' + str(p['start']) for p in parts]
            vectors = self._vectors()
            # Per-batch cancellation by worker termination leaves no active SQL
            # generation; retry reuses the deterministic vector IDs.
            for begin in range(0, len(parts), 128):
                batch = parts[begin:begin+128]
                encoded = self.encoder.encode([p['text'] for p in batch])
                vectors.upsert(ids=ids[begin:begin+128], embeddings=encoded,
                    metadatas=[{'paper_id': pid, 'generation': generation} for p in batch])
            old_ids = [r[0] for r in self.db.execute('SELECT id FROM chunks WHERE paper_id=?', (pid,))]
            with self.db:
                for cid in old_ids:
                    self.db.execute('DELETE FROM lexical WHERE id=?', (cid,))
                self.db.execute('DELETE FROM chunks WHERE paper_id=?', (pid,))
                self.db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?)',
                    (pid, str(path), sha, self.fingerprint, len(text), paper.get('title', '')))
                self.db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',
                    [(cid, pid, p['start'], p['end'], p['parent_start'], p['parent_end'], p['section'], p['text']) for cid, p in zip(ids, parts)])
                self.db.executemany('INSERT INTO lexical(id,text) VALUES(?,?)', [(cid,p['text']) for cid,p in zip(ids,parts)])
            stale = list(set(old_ids) - set(ids))
            if stale:
                vectors.delete(ids=stale)
            return {'paper_id': pid, 'status': 'indexed', 'chunks': len(parts)}

    def prune(self, eligible_ids):
        with self._lock:
            stale = [r[0] for r in self.db.execute('SELECT paper_id FROM documents') if r[0] not in eligible_ids]
            for pid in stale:
                ids = [r[0] for r in self.db.execute('SELECT id FROM chunks WHERE paper_id=?', (pid,))]
                with self.db:
                    for cid in ids:
                        self.db.execute('DELETE FROM lexical WHERE id=?', (cid,))
                    self.db.execute('DELETE FROM chunks WHERE paper_id=?', (pid,))
                    self.db.execute('DELETE FROM documents WHERE paper_id=?', (pid,))
                if ids:
                    self._vectors().delete(ids=ids)
            return len(stale)

    def search(self, query, paper_ids=None, top_k=6):
        with self._lock:
            eligible = set(paper_ids) if paper_ids is not None else None
            documents = {}
            for row in self.db.execute('SELECT * FROM documents WHERE fingerprint=?', (self.fingerprint,)):
                if eligible is not None and row['paper_id'] not in eligible:
                    continue
                path = Path(row['path'])
                if not path.is_relative_to(self.root) or not path.is_file():
                    continue
                text = path.read_text('utf-8')
                if digest(text) == row['sha']:
                    documents[row['paper_id']] = (dict(row), text)
            if not documents:
                return []
            pids = list(documents)
            placeholders = ','.join('?' for _ in pids)
            terms = re.findall(r'[A-Za-z0-9_]{3,}|[\u3400-\u9fff]+', query)
            terms = [term if len(term) <= 8 else term[:8] for term in terms][:24]
            lexical = []
            if terms:
                match = ' OR '.join('"' + t.replace('"', '""') + '"' for t in terms)
                lexical = [r[0] for r in self.db.execute(f'SELECT lexical.id FROM lexical JOIN chunks c ON c.id=lexical.id WHERE lexical MATCH ? AND c.paper_id IN ({placeholders}) ORDER BY rank LIMIT 40', (match, *pids))]
            semantic = []
            if self.encoder.available:
                generations = [digest(pid + documents[pid][0]['sha'] + self.fingerprint) for pid in pids]
                collection = self._vectors()
                result = collection.query(query_embeddings=self.encoder.encode([query]),
                    where={'generation': {'$in': generations}}, n_results=min(40, max(1, self.db.execute(
                        f'SELECT COUNT(*) FROM chunks WHERE paper_id IN ({placeholders})', pids).fetchone()[0])), include=['distances'])
                semantic = result['ids'][0]
            scores, methods = {}, {}
            for kind, ranked in (('text', lexical), ('vector', semantic)):
                for rank, cid in enumerate(ranked):
                    scores[cid] = scores.get(cid, 0) + 1 / (60 + rank + 1)
                    methods.setdefault(cid, []).append(kind)
            hits, seen = [], set()
            for cid in sorted(scores, key=lambda key: (-scores[key], key)):
                row = self.db.execute('SELECT * FROM chunks WHERE id=?', (cid,)).fetchone()
                if not row or row['paper_id'] not in documents:
                    continue
                identity = (row['paper_id'], row['parent_start'], row['parent_end'])
                if identity in seen:
                    continue
                seen.add(identity)
                doc, text = documents[row['paper_id']]
                hits.append({'paper_id': row['paper_id'], 'title': doc['title'], 'section': row['section'],
                    'text': text[row['parent_start']:row['parent_end']], 'matched_text': row['text'],
                    'source_path': str(Path(doc['path']).relative_to(self.root)), 'document_sha256': doc['sha'],
                    'start': row['parent_start'], 'end': row['parent_end'], 'methods': methods[cid], 'rrf_score': scores[cid]})
                if len(hits) >= top_k:
                    break
            return hits

    def close(self):
        with self._lock:
            self.db.close()


def library_papers(db):
    return db.fetchall("SELECT p.* FROM papers p LEFT JOIN paper_organization o ON o.paper_id=p.id WHERE p.id NOT LIKE 'prior:%' AND COALESCE(p.retrieval_status,'')!='off_topic' AND (o.canonical_id IS NULL OR o.canonical_id=p.id)")


async def index_library(db, retriever, progress=None):
    import asyncio
    papers = library_papers(db)
    result = {'indexed': 0, 'reused': 0, 'missing': 0, 'failed': [], 'external_requests': 0}
    if not retriever.encoder.available:
        raise RuntimeError('本地向量模型缺失，请重新安装完整软件包')
    for count, paper in enumerate(papers, 1):
        try:
            path, text = await asyncio.to_thread(paper_source, paper, retriever.root)
            if path and text:
                outcome = await asyncio.to_thread(retriever.index, paper, path, text)
                key = 'reused' if outcome['status'] == 'reused' else 'indexed'
                result[key] += 1
            else:
                result['missing'] += 1
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception('Local fulltext index failed for %s',paper['id'])
            result['failed'].append({'paper_id': paper['id'], 'error': type(exc).__name__})
        if progress:
            await progress(paper.get('title', ''), count, len(papers))
    result['pruned'] = await asyncio.to_thread(retriever.prune, {p['id'] for p in papers})
    return {**result, 'index': retriever.status()}
