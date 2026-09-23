"""Stage public source from an allowlist; never copies a research workspace."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
TREES=('src','native','tests','.github','docs/assets')
FILES=('README.md','README.en.md','LICENSE','THIRD_PARTY.md','.gitignore','requirements.txt',
       'requirements-dev.txt','requirements-vector.txt','requirements-lock-windows-py312.txt',
       'scripts/build_native.py','scripts/build_desktop.py','scripts/create_icon.py','scripts/prepare_release.py','scripts/refresh_release.py',
       'scripts/backup_workspace.py','scripts/migrate_mysql.py','scripts/backup_mysql.py','scripts/rewrite_latest_report.py',
       'pytest.ini','docs/MYSQL.md','docs/ARCHITECTURE.md','docs/PRODUCT_PURPOSE.md','docs/DOMAIN_PROFILES.md','docs/WORKSPACE_GUIDE.md','docs/PUBLISHING.md','docs/RELEASE_NOTES.md','docs/MODEL_OUTPUT.md','docs/assets/readme/README.md')
SUFFIXES={'.py','.js','.cjs','.css','.html','.cpp','.h','.hpp','.txt','.md','.json','.yml','.yaml','.png','.svg'}
PATTERNS=(re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
          re.compile(rb'\bsk-[A-Za-z0-9_-]{24,}\b'),
          re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'))

def selected_files(root=ROOT):
    paths=[root/name for name in FILES]
    for tree in TREES:
        paths.extend(p for p in (root/tree).rglob('*') if p.is_file() and
                     '__pycache__' not in p.parts and p.suffix.lower() in SUFFIXES)
    for path in sorted(set(paths)):
        if not path.is_file():
            raise RuntimeError(f'Missing release file: {path.relative_to(root)}')
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError('Release file escapes project root')
        yield path

def local_secrets(root):
    from dotenv import dotenv_values
    # Compare against known local credentials without printing their values.
    secrets=[]
    for env in (root/'.env',root/'my_research/.env',root/'work/mysql.local.env'):
        if env.exists():
            secrets.extend(v.encode() for k,v in dotenv_values(env).items()
                           if v and len(v)>=12 and any(s in k.upper() for s in ('KEY','TOKEN','SECRET','PASSWORD')))
    # UI credentials may be DPAPI encrypted. Only compare plaintext in memory.
    import sys
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from src.runtime.settings import read_secrets
    for workspace in (root/'work/runtime/daily',root/'work/reader-mysql',root/'work/preview/workspace'):
        for value in read_secrets(workspace).values():
            if isinstance(value,str) and len(value)>=8:secrets.append(value.encode())
    return secrets

def scan(files, root=ROOT):
    secrets=local_secrets(root)
    failures=[]
    for path in files:
        data=path.read_bytes()
        if any(pattern.search(data) for pattern in PATTERNS) or any(value in data for value in secrets):
            failures.append(str(path.relative_to(root)))
    if failures:
        raise RuntimeError('Potential credential found in: '+', '.join(failures))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--desktop',type=Path,help='Check a public desktop folder for private data and credentials')
    args=parser.parse_args()
    if args.desktop:
        folder=args.desktop.resolve()
        if not (folder/'AIReader.exe').is_file():raise RuntimeError('Desktop executable missing')
        files=[p for p in folder.rglob('*') if p.is_file()]
        forbidden={'.env','.secrets.json','.database.json','.history-import.json','reader-workspace.json'}
        for p in files:
            if p.is_symlink() or not p.resolve().is_relative_to(folder):raise RuntimeError('Desktop file escapes package')
            if p.name in forbidden or p.suffix.lower() in {'.db','.sqlite','.sqlite3','.pdf','.sql','.log'}:
                raise RuntimeError('Private desktop file: '+str(p.relative_to(folder)))
        scan(files)
        print(f'Public desktop check passed: {len(files)} files')
        return
    files=list(selected_files())
    scan(files)
    manifest={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    if args.check_only:
        print(f'Public source check passed: {len(files)} files')
        return
    out=ROOT/'dist/public-source'
    # Never overwrite a prepared Git checkout or the user's later changes.
    if out.exists() or out.with_suffix('.zip').exists():
        raise RuntimeError('dist/public-source already exists; preserve or move it before exporting again')
    out.mkdir(parents=True,exist_ok=False)
    for path in files:
        dest=out/path.relative_to(ROOT)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,dest)
    (out/'SOURCE_SHA256.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with zipfile.ZipFile(out.with_suffix('.zip'),'w',zipfile.ZIP_DEFLATED) as archive:
        for path in out.rglob('*'):
            if path.is_file(): archive.write(path,Path(out.name)/path.relative_to(out))
    print(out)
    print(out.with_suffix('.zip'))

if __name__=='__main__':
    main()
