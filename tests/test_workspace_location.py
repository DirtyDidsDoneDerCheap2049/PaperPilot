import json
import pytest
from src.runtime.workspace_location import default_workspace


def test_default_without_local_config(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'user'))
    assert default_workspace(tmp_path) == tmp_path/'user/AIReader/workspace'


def test_packaged_relative_workspace_and_missing_target(tmp_path):
    package = tmp_path/'package'
    package.mkdir()
    (package/'reader-workspace.json').write_text(json.dumps({'workspace':'../daily'}))
    with pytest.raises(RuntimeError, match='missing'):
        default_workspace(package)
    db = tmp_path/'daily/db'
    db.mkdir(parents=True)
    (db/'ai_reader.db').touch()
    assert default_workspace(package) == (tmp_path/'daily').resolve()
