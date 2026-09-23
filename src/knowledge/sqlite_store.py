import sqlite3
from contextlib import contextmanager
from threading import RLock
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, research_field TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY, project_id TEXT, name TEXT NOT NULL,
    root_path TEXT NOT NULL, config_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT,
    status TEXT DEFAULT 'idle',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    role TEXT NOT NULL, content TEXT NOT NULL, metadata_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    agent_name TEXT NOT NULL, status TEXT NOT NULL,
    input_json TEXT, output_json TEXT, error TEXT,
    started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY, agent_run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL, args_json TEXT, result_json TEXT,
    status TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, authors_json TEXT,
    year INTEGER, venue TEXT, doi TEXT, arxiv_id TEXT,
    semantic_scholar_id TEXT, url TEXT, open_access_pdf_url TEXT,
    abstract TEXT, citation_count INTEGER DEFAULT 0,
    retrieval_status TEXT DEFAULT 'metadata_only', missing_reason TEXT,
    fulltext_path TEXT, parsed_markdown_path TEXT, parsed_json_path TEXT,
    confidence REAL DEFAULT 0.5,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS parse_jobs (
    id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, parser TEXT NOT NULL,
    task_id TEXT, batch_id TEXT, state TEXT NOT NULL,
    full_zip_url TEXT, markdown_url TEXT, err_msg TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS innovation_profiles (
    paper_id TEXT PRIMARY KEY, profile_json TEXT NOT NULL,
    extraction_model TEXT, evidence_json TEXT, confidence REAL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evidence_chunks (
    id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, source_type TEXT,
    section TEXT, page INTEGER, content TEXT NOT NULL, metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS gap_analyses (
    id TEXT PRIMARY KEY, session_id TEXT, direction TEXT NOT NULL,
    search_log_json TEXT, matrix_json TEXT, gaps_json TEXT,
    evidence_json TEXT, report_path TEXT, confidence REAL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class SQLiteStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._transaction_depth = 0
        self.conn.execute('PRAGMA busy_timeout=5000')
        self.conn.execute('PRAGMA journal_mode=WAL')

    @contextmanager
    def transaction(self):
        """Synchronous transaction: never hold this context across an await."""
        with self._lock:
            name = f'reader_sp_{self._transaction_depth}'
            self.conn.execute(f'SAVEPOINT {name}')
            self._transaction_depth += 1
            try:
                yield self
                self.conn.execute(f'RELEASE {name}')
            except BaseException:
                self.conn.execute(f'ROLLBACK TO {name}')
                self.conn.execute(f'RELEASE {name}')
                raise
            finally:
                self._transaction_depth -= 1

    def init_schema(self):
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def execute(self, sql: str, params: tuple = ()):
        with self._lock:
            c = self.conn.cursor()
            try:
                c.execute(sql, params)
                if not self._transaction_depth:
                    self.conn.commit()
                return c.lastrowid
            except BaseException:
                if not self._transaction_depth:
                    self.conn.rollback()
                raise
            finally:
                c.close()

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        c = self.conn.cursor()
        c.execute(sql, params)
        row = c.fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        c = self.conn.cursor()
        c.execute(sql, params)
        return [dict(row) for row in c.fetchall()]

    def close(self):
        self.conn.close()
