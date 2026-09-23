import json
import sqlite3
import pytest
from src.runtime.history_import import import_history,digest
from src.knowledge.sqlite_store import SQLiteStore


def legacy(path):
    path.mkdir()
    (path/'config.yaml').write_text('llm:\n  api_key: private-value\nembedding:\n  provider: remote\n')
    (path/'.env').write_text('SECRET=private-value')
    (path/'reports').mkdir()
    (path/'reports/history.md').write_text('original report')
    db=SQLiteStore(path/'db/ai_reader.db');db.init_schema()
    db.execute("INSERT INTO sessions(id,workspace_id,title,status) VALUES('s','old','history','busy')")
    db.execute("INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES('m','s','assistant','original content',?)",(json.dumps({'path':str(path/'reports/history.md')}),))
    db.execute("INSERT INTO papers(id,title,abstract,fulltext_path) VALUES('p','stereo','evidence',?)",(str(path/'reports/history.md'),))
    db.close()


def test_import_isolated_verified_and_idempotent(tmp_path):
    source=tmp_path/'source';target=tmp_path/'daily';legacy(source)
    before=digest(source/'db/ai_reader.db')
    result=import_history(source,target)
    assert result['rows']['sessions']==1
    assert digest(source/'db/ai_reader.db')==before
    assert not (target/'.env').exists()
    assert 'private-value' not in (target/'config.yaml').read_text()
    assert (target/'reports/history.md').read_text()=='original report'
    with sqlite3.connect(target/'db/ai_reader.db') as db:
        assert db.execute('SELECT fulltext_path FROM papers').fetchone()[0]==(target/'reports/history.md').as_posix()
        assert db.execute('SELECT status FROM sessions').fetchone()[0]=='idle'
        db.execute("UPDATE messages SET content='new daily record'")
    import_history(source,target)
    with sqlite3.connect(target/'db/ai_reader.db') as db:
        assert db.execute('SELECT content FROM messages').fetchone()[0]=='new daily record'
    from src.knowledge.vector_store import LocalSearchStore
    index=LocalSearchStore(target/'db/text_index.db')
    assert index.query('papers','stereo')
    index.close()


def test_refuses_existing_or_nested_target(tmp_path):
    source=tmp_path/'source';legacy(source)
    target=tmp_path/'existing';target.mkdir()
    with pytest.raises(ValueError):import_history(source,target)
    with pytest.raises(ValueError):import_history(source,source/'copy')
    with pytest.raises(ValueError):import_history(source,source)


def test_rejects_live_or_mysql_source(tmp_path):
    source=tmp_path/'source';legacy(source)
    (source/'db/ai_reader.db-wal').touch()
    with pytest.raises(ValueError):import_history(source,tmp_path/'copy')
    (source/'db/ai_reader.db-wal').unlink()
    (source/'.database.json').write_text('{}')
    with pytest.raises(ValueError):import_history(source,tmp_path/'copy')


def test_failed_import_does_not_publish_destination(tmp_path,monkeypatch):
    source=tmp_path/'source';legacy(source)
    import src.runtime.history_import as module
    def fail(*args):raise OSError('copy failure')
    monkeypatch.setattr(module.shutil,'copy2',fail)
    with pytest.raises(OSError):import_history(source,tmp_path/'copy')
    assert not (tmp_path/'copy').exists()
    assert not list(tmp_path.glob('.history-import-*'))
