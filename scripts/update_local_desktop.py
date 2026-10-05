"""Update dist/PaperPilot.exe while preserving the user's existing workspace."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCAL_WORKSPACE = 'work/preview/workspace'
WORKSPACES = {'current': LOCAL_WORKSPACE, 'preview': LOCAL_WORKSPACE}


def local_package_path(relative):
    """Keep runtime files beside the EXE; collect accompanying documents."""
    relative = Path(relative)
    if relative == Path('AIReader.exe'):
        return Path('PaperPilot.exe')
    if relative.parts[0] == '_internal':
        return relative
    return Path('package-docs') / relative


def io_path(path):
    """Use extended Windows paths so preservation checks never skip long files."""
    path = Path(path).absolute()
    value = str(path)
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return path
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)


def sha(path):
    with io_path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def workspace_inventory(workspace):
    readable_root = io_path(workspace)
    if readable_root.is_symlink() or (hasattr(readable_root, 'is_junction') and readable_root.is_junction()):
        raise RuntimeError('Linked workspace paths are not supported')
    protected = {}
    for path in readable_root.rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise RuntimeError('Linked workspace paths are not supported')
        if path.is_file():
            protected[path.relative_to(readable_root)] = sha(path)
    return protected


def update(name='current'):
    source = ROOT / 'dist/public-desktop/AIReader'
    target = ROOT / 'dist'
    workspace = ROOT / WORKSPACES[name]
    if not (workspace / 'db/ai_reader.db').is_file():
        raise RuntimeError('Workspace is missing; refusing to silently open an empty demo')
    if not target.resolve().is_relative_to((ROOT / 'dist').resolve()):
        raise RuntimeError('Package path escapes dist')
    for base in (source, target / '_internal', target / 'package-docs'):
        for path in [base, *base.rglob('*')]:
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise RuntimeError('Linked package paths are not supported')
    if os.name == 'nt':
        processes = subprocess.run(['powershell', '-NoProfile', '-Command',
            'Get-Process -Name AIReader,PaperPilot -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path'],
            text=True, capture_output=True, check=False)
        if str(target / 'PaperPilot.exe').lower() in processes.stdout.lower():
            raise RuntimeError('Close the selected Reader application before updating')
    selector = target / 'reader-workspace.json'
    if selector.exists():
        if selector.is_symlink() or selector.is_junction():
            raise RuntimeError('Linked workspace selectors are not supported')
        selected = (target / json.loads(selector.read_text(encoding='utf-8'))['workspace']).resolve()
        if selected != workspace.resolve():
            raise RuntimeError('The local executable points to another workspace; inspect before updating')
    subprocess.run([sys.executable, str(ROOT/'scripts/prepare_release.py'), '--desktop', str(source)], check=True)
    protected = workspace_inventory(workspace)
    source_files = {p.relative_to(source) for p in source.rglob('*') if p.is_file()
                    and p.name != 'Start AI Reader.vbs'}
    expected = {local_package_path(p) for p in source_files}
    target_files = {p.relative_to(target) for p in (target/'_internal/src/ui').rglob('*') if p.is_file()}
    obsolete = target_files - expected
    # Prune stale packaged UI modules only. Unknown local additions are preserved.
    removable = [p for p in obsolete if p.parts[:3] == ('_internal', 'src', 'ui')]
    for relative in sorted(source_files):
        destination = target / local_package_path(relative)
        if not destination.resolve().is_relative_to(target.resolve()) or destination.is_symlink():
            raise RuntimeError('Local package file escapes dist')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    for relative in removable:
        (target / relative).unlink()
    selector.write_text(json.dumps({
        'workspace': os.path.relpath(workspace, target)
    }), encoding='utf-8')
    if workspace_inventory(workspace) != protected:
        raise RuntimeError('Workspace changed during update; inspect before proceeding')
    if any(sha(source/p) != sha(target/local_package_path(p)) for p in source_files):
        raise RuntimeError('Package copy verification failed')
    result = {'package': 'dist/PaperPilot.exe', 'workspace': WORKSPACES[name],
              'workspace_files_unchanged': len(protected), 'exe_sha256': sha(target/'PaperPilot.exe'),
              'removed_obsolete_ui_files': [p.as_posix() for p in removable]}
    (target/'LOCAL_DESKTOP.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=WORKSPACES, default='current',
                        help='Legacy option; both names update the single local executable')
    update(parser.parse_args().target)
