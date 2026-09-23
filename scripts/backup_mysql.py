"""Export a consistent MySQL snapshot while the task engine is stopped."""
import argparse
from pathlib import Path
import os
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.knowledge.mysql_store import MySQLStore,research_tables
from src.runtime.database_config import load_database_config

def export_sql(db,destination):
    destination=Path(destination)
    if destination.exists():raise RuntimeError('Backup destination already exists')
    lock='reader:'+os.environ['MYSQL_DATABASE']
    if db.fetchone('SELECT GET_LOCK(?,0) AS acquired',(lock,))['acquired']!=1:
        raise RuntimeError('请关闭 Reader 后再备份')
    allowed=set(research_tables())|{'jobs','events','search_documents','reader_schema'}
    try:
        with db.conn.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            cursor.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
            cursor.execute('SHOW TABLES')
            tables=[next(iter(row.values())) for row in cursor.fetchall() if next(iter(row.values())) in allowed]
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open('x',encoding='utf-8') as output:
                output.write('-- Reader backup. Restore only into an EMPTY database.\nSET NAMES utf8mb4;\nSET time_zone=\'+00:00\';\n')
                for table in tables:
                    cursor.execute(f'SHOW CREATE TABLE `{table}`')
                    output.write(cursor.fetchone()['Create Table']+';\n')
                    cursor.execute(f'SHOW COLUMNS FROM `{table}`')
                    fields=[r['Field'] for r in cursor.fetchall()
                            if not any(kind in r['Extra'] for kind in ('VIRTUAL GENERATED','STORED GENERATED'))]
                    cursor.execute('SELECT '+','.join(f'`{field}`' for field in fields)+f' FROM `{table}`')
                    for row in cursor.fetchall():
                        output.write('INSERT INTO `'+table+'` ('+','.join(f'`{f}`' for f in fields)+') VALUES ('+
                                     ','.join(db.conn.escape(row[field]) for field in fields)+');\n')
                output.write('-- READER_BACKUP_COMPLETE\n')
        db.conn.rollback()
    finally:
        db.conn.rollback()
        db.fetchone('SELECT RELEASE_LOCK(?) AS released',(lock,))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-env',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    load_database_config(Path.cwd(),args.db_env)
    db=MySQLStore()
    try:export_sql(db,args.output);print('Database backup complete')
    finally:db.close()
