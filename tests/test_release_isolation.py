from pathlib import Path
import importlib.util
import pytest

spec=importlib.util.spec_from_file_location('release',Path(__file__).resolve().parents[1]/'scripts/prepare_release.py')
release=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_private_workspace_never_selected(tmp_path):
    for name in release.FILES:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('public')
    for name in ('work/runtime/daily/.secrets.json','my_research/db/ai_reader.db','.env','docs/history/private.md'):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('private')
    assert not any('private' in p.read_text() for p in release.selected_files(tmp_path))


def test_known_credential_blocks_export_without_disclosure(tmp_path,monkeypatch):
    secret='fixture-'+'sensitive-value'
    monkeypatch.setattr(release,'local_secrets',lambda root:[secret.encode()])
    p=tmp_path/'source.py';p.write_text(secret)
    with pytest.raises(RuntimeError) as error:release.scan([p],tmp_path)
    assert 'source.py' in str(error.value)
    assert secret not in str(error.value)


def test_symlink_cannot_export_external_file(tmp_path,monkeypatch):
    target=tmp_path/'private.txt';target.write_text('private')
    root=tmp_path/'root';root.mkdir()
    try:(root/'public.py').symlink_to(target)
    except OSError:pytest.skip('symlink privilege unavailable')
    monkeypatch.setattr(release,'FILES',('public.py',))
    monkeypatch.setattr(release,'TREES',())
    with pytest.raises(RuntimeError):list(release.selected_files(root))
