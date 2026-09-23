"""Refresh verified public staging and ZIPs after tests and a desktop build.

Refuses to overwrite changed or unknown source files. Local workspaces and
private desktop selectors are not copied into either public artifact.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from prepare_release import ROOT, scan, selected_files


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe(base, relative):
    path = base/relative
    if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
        raise RuntimeError('Release path escapes staging')
    return path


def main():
    public = ROOT/'dist/public-source'
    desktop = ROOT/'dist/public-desktop/AIReader'
    old = json.loads((public/'SOURCE_SHA256.json').read_text(encoding='utf-8'))
    actual = {p.relative_to(public).as_posix() for p in public.rglob('*')
              if p.is_file() and '.git' not in p.relative_to(public).parts}
    if actual != set(old)|{'SOURCE_SHA256.json'}:
        raise RuntimeError('Public source contains unknown files; review them before refresh')
    for name, digest in old.items():
        if sha(safe(public, name)) != digest:
            raise RuntimeError('Public source was edited: '+name)
    sources = list(selected_files())
    scan(sources)
    manifest = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources}
    if set(old)-set(manifest):
        raise RuntimeError('Source removals need explicit review before refreshing the release')
    # Documentation-only updates do not require rebuilding the executable.
    # Keep the folder and ZIP documentation in sync with the exported source.
    for source in sources:
        relative=source.relative_to(ROOT)
        if relative.parts[0]=='docs' or relative.as_posix() in {'README.md','README.en.md','THIRD_PARTY.md','LICENSE'}:
            destination=safe(desktop,relative)
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,destination)
    shutil.copytree(ROOT/'native/licenses',desktop/'THIRD_PARTY_LICENSES',dirs_exist_ok=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/prepare_release.py'),'--desktop',str(desktop)],check=True)
    for path in (ROOT/'src/ui/static').iterdir():
        if path.is_file() and sha(path) != sha(desktop/'_internal/src/ui/static'/path.name):
            raise RuntimeError('Desktop UI is stale; build the desktop first')
    for source in sources:
        dest = safe(public, source.relative_to(ROOT))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    (public/'SOURCE_SHA256.json').write_text(json.dumps(manifest, indent=2),encoding='utf-8')
    artifacts = []
    for folder, filename in ((public,'public-source.zip'),(desktop,'AIReader-windows-preview.zip')):
        target = ROOT/'dist'/filename
        temp = target.with_suffix('.zip.tmp')
        names = sorted(set(manifest)|{'SOURCE_SHA256.json'}) if folder == public else sorted(
            p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file())
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as z:
            for name in names:
                z.write(safe(folder,name),folder.name+'/'+name)
        with zipfile.ZipFile(temp) as z:
            if z.testzip() is not None:
                raise RuntimeError('Release ZIP failed integrity check')
        temp.replace(target)
        artifacts.append({'file':filename,'sha256':sha(target),'bytes':target.stat().st_size})
    bundle = ROOT/'dist/AIReader-windows-preview.zip'
    bundle.with_suffix('.zip.sha256').write_text(sha(bundle)+'  '+bundle.name+'\n',encoding='ascii')
    record_path = ROOT/'dist/DELIVERY_MANIFEST.json'
    record = json.loads(record_path.read_text(encoding='utf-8')) if record_path.exists() else {}
    record.update(status='local research-agent preview; no remote upload',artifacts=artifacts,
                  exe_sha256=sha(desktop/'AIReader.exe'))
    validation = ROOT/'work/delivery-validation.json'
    if validation.exists():
        record['validation'] = json.loads(validation.read_text(encoding='utf-8'))
    record_path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifacts':artifacts,'exe_sha256':record['exe_sha256']},indent=2))


if __name__ == '__main__':
    main()
