import os
import uuid
import pytest

pytestmark=pytest.mark.mysql

@pytest.fixture
def mysql_db(monkeypatch):
    if os.getenv('READER_MYSQL_TEST')!='1':pytest.skip('Enable READER_MYSQL_TEST=1 for a disposable MySQL instance')
    import pymysql
    name='reader_test_'+uuid.uuid4().hex[:16]
    # Never uses the user's normal connection; explicit test port only.
    port=int(os.getenv('READER_MYSQL_TEST_PORT','33079'))
    admin=pymysql.connect(host='127.0.0.1',port=port,user='root',password=os.getenv('READER_MYSQL_TEST_PASSWORD',''),autocommit=True)
    with admin.cursor() as cur:cur.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
    for key,value in {'MYSQL_HOST':'127.0.0.1','MYSQL_PORT':str(port),'MYSQL_USER':'root','MYSQL_PASSWORD':os.getenv('READER_MYSQL_TEST_PASSWORD',''),'MYSQL_DATABASE':name,'READER_STORAGE':'mysql'}.items():monkeypatch.setenv(key,value)
    from src.knowledge.mysql_store import MySQLStore
    db=MySQLStore()
    try:
        db.init_schema()
        yield db
    finally:
        db.close()
        with admin.cursor() as cur:cur.execute(f'DROP DATABASE `{name}`')
        admin.close()

def test_schema_transaction_unicode_and_duplicates(mysql_db):
    db=mysql_db
    db.execute('INSERT INTO sessions(id,workspace_id,title) VALUES(?,?,?)',('s','w','中文 📚'))
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute("UPDATE sessions SET title='wrong' WHERE id='s'")
            with db.transaction():db.execute("UPDATE sessions SET title='nested' WHERE id='s'")
            raise RuntimeError('rollback')
    assert db.fetchone("SELECT title FROM sessions WHERE id='s'")['title']=='中文 📚'
    db.execute('INSERT OR IGNORE INTO sessions(id,workspace_id,title) VALUES(?,?,?)',('s','w','duplicate'))
    assert db.fetchone("SELECT title FROM sessions WHERE id='s'")['title']=='中文 📚'
    assert db.fetchone("SELECT '?' AS literal,? AS value",('50% 中文?',))['value']=='50% 中文?'

def test_native_mysql_lifecycle(mysql_db,tmp_path):
    from tests.test_desktop_delivery import Native
    first=Native(tmp_path/'tasks.db')
    try:
        assert first.call('ping')['data']['engine']=='cpp17-mysql'
        assert first.submit()['ok']
        assert first.submit(id='duplicate')['data']['id']=='j'
        token=first.call('claim')['data']['token']
        assert not first.call('finish',id='j',token=token-1,status='succeeded')['ok']
        assert first.call('cancel',id='j')['ok']
        assert first.call('finish',id='j',token=token,status='succeeded')['data']['status']=='cancelled'
    finally:first.close()
    assert not (tmp_path/'tasks.db').exists()
    assert mysql_db.fetchone('SELECT status FROM jobs WHERE id=?',('j',))['status']=='cancelled'

def test_mysql_actual_worker(mysql_db,tmp_path,monkeypatch):
    from tests.test_desktop_delivery import test_worker_through_native_and_local_provider
    test_worker_through_native_and_local_provider(tmp_path,monkeypatch)

def test_mysql_pdf_report(mysql_db,tmp_path,monkeypatch):
    from tests.test_offline_report import test_local_pdf_to_report_without_external_services
    test_local_pdf_to_report_without_external_services(tmp_path,monkeypatch)

def test_first_run_connection_starts_backend_and_saves_config(mysql_db,tmp_path,monkeypatch):
    import webview
    from src.app.database_setup import run_setup
    from src.runtime.settings import read_secrets
    values={key:os.environ[key] for key in ('MYSQL_HOST','MYSQL_PORT','MYSQL_USER','MYSQL_PASSWORD','MYSQL_DATABASE')}
    state={}
    class Window:
        def load_url(self,url):state['url']=url
    def create_window(*args,**kwargs):
        state['api']=kwargs['js_api']
        assert '连接你的 MySQL' in kwargs['html']
        return Window()
    def start(*args,**kwargs):
        assert state['api'].connect(values)=={'ok':True}
        assert state['url'].startswith('http://127.0.0.1:')
        assert (tmp_path/'.database.json').is_file()
        assert read_secrets(tmp_path)['mysql_password']==values['MYSQL_PASSWORD']
    monkeypatch.setattr(webview,'create_window',create_window)
    monkeypatch.setattr(webview,'start',start)
    run_setup(tmp_path)

def test_mysql_native_crash_recovery(mysql_db,tmp_path):
    from tests.test_desktop_delivery import test_native_crash_recovery_and_retry
    test_native_crash_recovery_and_retry(tmp_path)

def test_sqlite_import_verified_and_refuses_merge(mysql_db,tmp_path):
    from src.knowledge.sqlite_store import SQLiteStore
    from scripts.migrate_mysql import migrate
    source=tmp_path/'old.db';old=SQLiteStore(source);old.init_schema()
    old.execute('INSERT INTO sessions(id,workspace_id,title) VALUES(?,?,?)',('s','w','中文'))
    old.execute('INSERT INTO messages(id,session_id,role,content) VALUES(?,?,?,?)',('m','s','user','fixture'))
    old.close()
    before=source.read_bytes()
    report=migrate(source,mysql_db)
    assert report['messages']['rows']==1 and source.read_bytes()==before
    with pytest.raises(RuntimeError):migrate(source,mysql_db)

def test_backup_restores_timestamps_and_generated_columns(mysql_db,tmp_path):
    import pymysql
    from scripts.backup_mysql import export_sql
    from tests.test_desktop_delivery import Native
    db=mysql_db
    db.execute('INSERT INTO sessions(id,workspace_id,title,created_at) VALUES(?,?,?,?)',
               ('s','w',"引号 ' 与换行\n备份",'2024-02-03 04:05:06.123456'))
    native=Native(tmp_path/'tasks.db')
    try:
        assert native.submit()['ok']
        with pytest.raises(RuntimeError):export_sql(db,tmp_path/'busy.sql')
    finally:native.close()
    backup=tmp_path/'backup.sql';export_sql(db,backup)
    from src.knowledge.mysql_store import connection_options
    options=connection_options(False)
    restore_name='reader_restore_'+uuid.uuid4().hex[:16]
    admin=pymysql.connect(**options)
    restored=None
    try:
        with admin.cursor() as cur:cur.execute(f'CREATE DATABASE `{restore_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
        restored=pymysql.connect(**options,database=restore_name,client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS)
        with restored.cursor() as cur:
            cur.execute(backup.read_text(encoding='utf-8'))
            while cur.nextset():pass
            cur.execute('SELECT * FROM sessions');actual=cur.fetchall()
            assert [db._row(row) for row in actual]==db.fetchall('SELECT * FROM sessions')
            cur.execute('SELECT * FROM jobs');actual=cur.fetchall()
            assert [db._row(row) for row in actual]==db.fetchall('SELECT * FROM jobs')
    finally:
        if restored:restored.close()
        with admin.cursor() as cur:cur.execute(f'DROP DATABASE IF EXISTS `{restore_name}`')
        admin.close()
