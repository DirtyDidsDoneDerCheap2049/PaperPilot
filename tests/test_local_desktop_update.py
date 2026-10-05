import json
from types import SimpleNamespace

import pytest

from scripts import update_local_desktop as updater


@pytest.fixture
def local_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, 'ROOT', tmp_path)
    monkeypatch.setattr(updater.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=''))
    source = tmp_path / 'dist/public-desktop/AIReader'
    for relative, data in {
        'AIReader.exe': b'packaged-runtime',
        '_internal/runtime.dll': b'dependency',
        'docs/guide.md': b'guide',
        'Start AI Reader.vbs': b'legacy-launcher',
    }.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    workspace = tmp_path / updater.LOCAL_WORKSPACE
    (workspace / 'db').mkdir(parents=True)
    (workspace / 'db/ai_reader.db').write_bytes(b'user-records-after-deletion')
    (workspace / '.secrets.json').write_bytes(b'encrypted-settings')
    return tmp_path, workspace


def test_update_and_legacy_alias_keep_one_exe_and_existing_data(local_layout):
    root, workspace = local_layout
    before = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    updater.update('current')
    updater.update('preview')
    assert (root / 'dist/PaperPilot.exe').read_bytes() == b'packaged-runtime'
    assert (root / 'dist/_internal/runtime.dll').read_bytes() == b'dependency'
    assert (root / 'dist/package-docs/docs/guide.md').read_bytes() == b'guide'
    selector = json.loads((root / 'dist/reader-workspace.json').read_text())
    assert (root / 'dist' / selector['workspace']).resolve() == workspace
    assert not (root / 'dist/current').exists()
    assert not (root / 'dist/preview').exists()
    assert before == {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}


def test_existing_workspace_choice_is_not_silently_changed(local_layout):
    root, _ = local_layout
    (root / 'dist/PaperPilot.exe').write_bytes(b'previous-exe')
    (root / 'dist/reader-workspace.json').write_text(json.dumps({'workspace': '../another-workspace'}))
    with pytest.raises(RuntimeError, match='another workspace'):
        updater.update()
    assert (root / 'dist/PaperPilot.exe').read_bytes() == b'previous-exe'


def test_missing_workspace_does_not_create_empty_demo(local_layout):
    root, workspace = local_layout
    (workspace / 'db/ai_reader.db').unlink()
    with pytest.raises(RuntimeError, match='Workspace is missing'):
        updater.update()
    assert not (root / 'dist/PaperPilot.exe').exists()
    assert not (workspace / 'db/ai_reader.db').exists()


def test_update_detects_changes_to_long_workspace_file(local_layout, monkeypatch):
    root, workspace = local_layout
    relative = ('segment-' + 'a' * 65) + '/' + ('segment-' + 'b' * 65) + '/' + ('segment-' + 'c' * 65) + '/paper.pdf'
    paper = updater.io_path(workspace / relative)
    paper.parent.mkdir(parents=True)
    paper.write_bytes(b'original-paper')
    inventory = updater.workspace_inventory(workspace)
    assert (workspace / relative).relative_to(workspace) in inventory
    copy = updater.shutil.copy2

    def change_during_copy(source, destination):
        result = copy(source, destination)
        paper.write_bytes(b'changed-paper')
        return result

    monkeypatch.setattr(updater.shutil, 'copy2', change_during_copy)
    with pytest.raises(RuntimeError, match='Workspace changed during update'):
        updater.update()
