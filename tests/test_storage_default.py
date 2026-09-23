import json
import pytest


def test_fresh_workspace_without_database_server(tmp_path, monkeypatch):
    from src.runtime.database_config import load_database_config
    from src.app.factory import build_app
    from fastapi.testclient import TestClient
    monkeypatch.delenv('READER_STORAGE', raising=False)
    for key in ('MYSQL_USER','MYSQL_DATABASE','MYSQL_PASSWORD'):
        monkeypatch.delenv(key, raising=False)
    load_database_config(tmp_path)
    app, _ = build_app(tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        settings = client.get('/api/settings').json()
        assert settings['storage']['backend'] == 'sqlite'
        assert (tmp_path/'db/ai_reader.db').exists()
        assert (tmp_path/'db/tasks.db').exists()


def test_existing_mysql_workspace_stays_mysql(tmp_path, monkeypatch):
    from src.runtime.database_config import storage_backend
    monkeypatch.delenv('READER_STORAGE', raising=False)
    (tmp_path/'.database.json').write_text(json.dumps({'MYSQL_DATABASE':'existing'}))
    assert storage_backend(tmp_path) == 'mysql'
    monkeypatch.setenv('READER_STORAGE','sqlite')
    assert storage_backend(tmp_path) == 'sqlite'


def test_corrupt_config_does_not_silently_create_empty_workspace(tmp_path, monkeypatch):
    from src.runtime.database_config import storage_backend
    monkeypatch.delenv('READER_STORAGE', raising=False)
    (tmp_path/'.database.json').write_text('{broken')
    with pytest.raises(ValueError):
        storage_backend(tmp_path)


def test_explicit_connection_file_selects_mysql(tmp_path, monkeypatch):
    from src.runtime.database_config import load_database_config, storage_backend
    monkeypatch.delenv('READER_STORAGE', raising=False)
    # Connection selection only; no server or secret involved.
    env = tmp_path/'connection.env'
    env.write_text('MYSQL_USER=reader\nMYSQL_DATABASE=reader_test\n')
    load_database_config(tmp_path, env)
    assert storage_backend(tmp_path) == 'mysql'
