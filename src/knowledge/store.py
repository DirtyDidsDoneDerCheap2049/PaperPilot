from pathlib import Path

def open_store(workspace):
    from src.runtime.database_config import storage_backend
    backend=storage_backend(workspace)
    if backend=='sqlite':
        from src.knowledge.sqlite_store import SQLiteStore
        return SQLiteStore(Path(workspace)/'db/ai_reader.db')
    if backend!='mysql':raise ValueError('Unsupported database backend')
    from src.knowledge.mysql_store import MySQLStore
    return MySQLStore()
