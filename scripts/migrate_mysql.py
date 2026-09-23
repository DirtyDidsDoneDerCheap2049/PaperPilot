"""Read-only SQLite import into an EMPTY Reader MySQL database, with row verification."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.knowledge.mysql_store import MySQLStore,research_tables
from src.runtime.database_config import load_database_config

def normalized(value):
    if isinstance(value,str):
        try:
            if len(value)>=19 and value[4]=='-' and value[10] in ' T':
                return datetime.fromisoformat(value).isoformat(sep=' ')
        except ValueError:pass
    return value

def rows_digest(rows,columns):
    payload=sorted(json.dumps([normalized(row[c]) for c in columns],ensure_ascii=False,sort_keys=True,default=str) for row in rows)
    return hashlib.sha256('\n'.join(payload).encode()).hexdigest()

def migrate(source,target):
    lock='reader:'+os.environ['MYSQL_DATABASE']
    if target.fetchone('SELECT GET_LOCK(?,0) AS acquired',(lock,))['acquired']!=1:
        raise RuntimeError('请关闭 Reader 后再迁移')
    try:
        return _migrate_locked(source,target)
    finally:
        target.fetchone('SELECT RELEASE_LOCK(?) AS released',(lock,))

def _migrate_locked(source,target):
    source=Path(source).resolve()
    # mode=ro prevents database writes; transaction gives a consistent snapshot.
    sqlite=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    sqlite.row_factory=sqlite3.Row
    result={}
    try:
        sqlite.execute('BEGIN')
        available={r[0] for r in sqlite.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tables=[name for name in research_tables() if name in available]
        with target.transaction():
            for name in tables:
                if target.fetchone(f'SELECT COUNT(*) AS n FROM `{name}`')['n']:
                    raise RuntimeError(f'Target table is not empty: {name}; import aborted')
            for name in tables:
                columns=[r['name'] for r in sqlite.execute(f'PRAGMA table_info("{name}")')]
                rows=[dict(row) for row in sqlite.execute(f'SELECT rowid AS _seq,* FROM "{name}" ORDER BY rowid')]
                # _seq preserves ordering of messages created in the same second.
                fields=columns+['_seq']
                statement=f'INSERT INTO `{name}` ('+','.join(f'`{c}`' for c in fields)+') VALUES ('+','.join('?' for _ in fields)+')'
                for row in rows:
                    values=[normalized(row[c]) if c.endswith('_at') else row[c] for c in fields]
                    target.execute(statement,tuple(values))
                copied=target.fetchall('SELECT '+','.join(f'`{c}`' for c in fields)+f' FROM `{name}` ORDER BY _seq')
                if len(copied)!=len(rows) or rows_digest(rows,fields)!=rows_digest(copied,fields):
                    raise RuntimeError(f'Row verification failed: {name}; import rolled back')
                result[name]={'rows':len(rows),'sha256':rows_digest(copied,fields)}
        return result
    finally:sqlite.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--db-env',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    load_database_config(args.source.parent.parent,args.db_env)
    db=MySQLStore()
    try:
        db.init_schema()
        result=migrate(args.source,db)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Import verified:',sum(item['rows'] for item in result.values()),'rows;',len(result),'tables')
    finally:db.close()

if __name__=='__main__':main()
