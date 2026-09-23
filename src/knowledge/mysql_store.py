"""MySQL 8 storage behind the existing parameterized store interface."""
import os
import re
from contextlib import contextmanager
from datetime import date, datetime
from threading import RLock

import pymysql
from src.knowledge.sqlite_store import SCHEMA_SQL


def connection_options(database=True):
    required=('MYSQL_USER','MYSQL_DATABASE')
    if any(not os.getenv(key) for key in required):
        raise RuntimeError('请配置 MySQL 连接：MYSQL_HOST、MYSQL_PORT、MYSQL_USER、MYSQL_PASSWORD、MYSQL_DATABASE')
    name=os.environ['MYSQL_DATABASE']
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',name):
        raise ValueError('MySQL 数据库名称仅支持字母、数字和下划线，以字母开头')
    result=dict(host=os.getenv('MYSQL_HOST','127.0.0.1'),port=int(os.getenv('MYSQL_PORT','3306')),
                user=os.environ['MYSQL_USER'],password=os.getenv('MYSQL_PASSWORD',''),
                charset='utf8mb4',autocommit=True,connect_timeout=8,read_timeout=30,write_timeout=30,
                cursorclass=pymysql.cursors.DictCursor)
    if database:result['database']=name
    if os.getenv('MYSQL_SSL_CA'):result['ssl']={'ca':os.environ['MYSQL_SSL_CA'],'check_hostname':True}
    return result


def research_tables():
    tables={}
    for name,body in re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\);',SCHEMA_SQL,re.S):
        columns=[]
        for field in re.split(r',\s*(?![^()]*\))',body.strip()):
            key,kind,*rest=field.strip().split()
            suffix=' '.join(rest)
            if 'PRIMARY KEY' in suffix:
                kind='VARCHAR(255) COLLATE utf8mb4_bin'
            elif key in {'status','retrieval_status','state','role','parser','source_type'}:
                kind='VARCHAR(64)'
            elif key.endswith('_at'):
                kind='DATETIME(6)'
            else:kind={'TEXT':'LONGTEXT','REAL':'DOUBLE','INTEGER':'INT'}.get(kind,kind)
            suffix=suffix.replace("(datetime('now'))",'CURRENT_TIMESTAMP(6)')
            columns.append(f'`{key}` {kind} {suffix}')
        columns.append('`_seq` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT UNIQUE')
        tables[name]='CREATE TABLE IF NOT EXISTS `'+name+'` ('+', '.join(columns)+') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin'
    return tables


def mysql_sql(sql):
    """Translate only the legacy dialect used in this repository; bind data separately."""
    sql=re.sub(r"datetime\('now'\)",'UTC_TIMESTAMP(6)',sql,flags=re.I)
    ignore=bool(re.search(r'INSERT\s+OR\s+IGNORE',sql,re.I))
    sql=re.sub(r'INSERT\s+OR\s+IGNORE','INSERT',sql,flags=re.I)
    sql=re.sub(r'INSERT\s+OR\s+REPLACE','REPLACE',sql,flags=re.I)
    sql=re.sub(r'ON CONFLICT\(\w+\) DO UPDATE SET','ON DUPLICATE KEY UPDATE',sql,flags=re.I)
    sql=re.sub(r'excluded\.(\w+)',r'VALUES(\1)',sql)
    # Do not replace question marks/identifiers embedded in quoted SQL literals.
    chunks=re.split(r"('(?:''|[^'])*')",sql)
    for i in range(0,len(chunks),2):
        chunks[i]=re.sub(r'\browid\b','_seq',chunks[i],flags=re.I)
    sql=''.join(chunks).replace('%','%%')
    chunks=re.split(r"('(?:''|[^'])*')",sql)
    for i in range(0,len(chunks),2):chunks[i]=chunks[i].replace('?','%s')
    sql=''.join(chunks)
    if ignore:
        match=re.search(r'INSERT\s+INTO\s+(\w+)',sql,re.I)
        key='paper_id' if match and match[1]=='innovation_profiles' else 'id'
        sql=sql.rstrip().rstrip(';')+f' ON DUPLICATE KEY UPDATE `{key}`=`{key}`'
    return sql


class MySQLStore:
    backend='mysql'
    def __init__(self):
        self._lock=RLock();self._depth=0
        try:self.conn=pymysql.connect(**connection_options())
        except pymysql.MySQLError as exc:
            raise RuntimeError(f'MySQL 连接失败（错误码 {exc.args[0]}），请检查服务、专用账号和数据库授权') from None
        with self.conn.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
            cursor.execute('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')

    def init_schema(self):
        with self._lock,self.conn.cursor() as cursor:
            for sql in research_tables().values():cursor.execute(sql)
            cursor.execute('CREATE TABLE IF NOT EXISTS reader_schema(version INT PRIMARY KEY, applied_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6)) ENGINE=InnoDB')
            # Prefix indexes cover filtering on legacy long-text foreign identifiers.
            for table,column in (('messages','session_id'),('agent_runs','session_id'),('tool_calls','agent_run_id'),('gap_analyses','session_id'),('evidence_chunks','paper_id'),('parse_jobs','paper_id')):
                index='reader_'+column
                cursor.execute('SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name=%s AND index_name=%s',(table,index))
                if not cursor.fetchone():cursor.execute(f'CREATE INDEX `{index}` ON `{table}` (`{column}`(191))')
            cursor.execute('INSERT INTO reader_schema(version) VALUES(1) ON DUPLICATE KEY UPDATE version=version')

    @contextmanager
    def transaction(self):
        with self._lock:
            depth=self._depth
            with self.conn.cursor() as cur:
                if depth:cur.execute(f'SAVEPOINT reader_sp_{depth}')
                else:self.conn.begin()
            self._depth+=1
            try:
                yield self
                if depth:
                    with self.conn.cursor() as cur:cur.execute(f'RELEASE SAVEPOINT reader_sp_{depth}')
                else:self.conn.commit()
            except BaseException:
                if depth:
                    with self.conn.cursor() as cur:cur.execute(f'ROLLBACK TO SAVEPOINT reader_sp_{depth}')
                else:self.conn.rollback()
                raise
            finally:self._depth-=1

    def execute(self,sql,params=()):
        with self._lock,self.conn.cursor() as cur:
            cur.execute(mysql_sql(sql),params)
            return cur.lastrowid

    @staticmethod
    def _row(row):
        return {k:v.isoformat(sep=' ') if isinstance(v,datetime) else v.isoformat() if isinstance(v,date) else v for k,v in row.items()}

    def fetchall(self,sql,params=()):
        with self._lock,self.conn.cursor() as cur:
            cur.execute(mysql_sql(sql),params)
            return [self._row(row) for row in cur.fetchall()]

    def fetchone(self,sql,params=()):
        with self._lock,self.conn.cursor() as cur:
            cur.execute(mysql_sql(sql),params)
            row=cur.fetchone()
            return self._row(row) if row else None

    def close(self):self.conn.close()


class MySQLSearchStore:
    mode='MySQL 全文检索（无需向量 API）'
    def __init__(self,db):
        self.db=db
        db.execute('CREATE TABLE IF NOT EXISTS search_documents(id VARCHAR(255) NOT NULL, collection VARCHAR(64) NOT NULL, content LONGTEXT NOT NULL, metadata LONGTEXT NOT NULL, PRIMARY KEY(collection,id), FULLTEXT KEY content_search(content)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')

    def add_documents(self,collection,docs):
        import json
        with self.db.transaction():
            for doc in docs:
                self.db.execute('INSERT INTO search_documents(id,collection,content,metadata) VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE content=VALUES(content),metadata=VALUES(metadata)',
                                (doc.id,collection,doc.text,json.dumps(doc.metadata,ensure_ascii=False)))

    def query(self,collection,query,top_k=10,where=None):
        import json
        from src.knowledge.vector_store import VectorHit
        rows=self.db.fetchall('SELECT id,content,metadata,MATCH(content) AGAINST(? IN NATURAL LANGUAGE MODE) AS score FROM search_documents WHERE collection=? AND MATCH(content) AGAINST(? IN NATURAL LANGUAGE MODE)>0 ORDER BY score DESC LIMIT ?',
                              (query,collection,query,max(1,min(top_k,100))*5))
        hits=[]
        for row in rows:
            meta=json.loads(row['metadata'])
            if where and any(meta.get(k)!=v for k,v in where.items()):continue
            hits.append(VectorHit(id=row['id'],text=row['content'],metadata=meta,distance=-float(row['score'])))
        return hits[:top_k]
    def close(self):pass
