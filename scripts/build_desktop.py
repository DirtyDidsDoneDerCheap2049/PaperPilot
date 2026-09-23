"""Build a Windows folder distribution; run from the pinned development environment."""
import os
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--local-workspace',action='store_true',help='Build a private local package pointing to work/runtime/daily')
    args=parser.parse_args()
    dist_dir='dist/current' if args.local_workspace else 'dist/public-desktop'
    if os.name != 'nt':
        raise SystemExit('This release build targets Windows.')
    if not (ROOT/'build/reader_tasks.exe').is_file():
        subprocess.run([sys.executable,str(ROOT/'scripts/build_native.py')],check=True)
    from create_icon import create_icon
    icon=create_icon(ROOT/'build/reader.ico')
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--console','--hide-console','hide-early',
             '--name','AIReader','--icon',str(icon),'--distpath',str(ROOT/dist_dir),
             '--workpath',str(ROOT/'build/pyinstaller'),'--specpath',str(ROOT/'build'),
             '--paths',str(ROOT),'--add-data',f'{ROOT / "src/ui/static"};src/ui/static',
             '--add-binary',f'{ROOT / "build/reader_tasks.exe"};build',
             '--collect-submodules','src', '--collect-data','webview',
             '--exclude-module','chromadb','--exclude-module','torch',
             '--exclude-module','sentence_transformers','--exclude-module','pytest',
             '--hidden-import','uvicorn.logging','--hidden-import','uvicorn.loops.auto',
             '--hidden-import','uvicorn.protocols.http.auto','--hidden-import','uvicorn.protocols.websockets.auto',
             '--hidden-import','uvicorn.protocols.websockets.websockets_impl',
             '--hidden-import','uvicorn.lifespan.on',str(ROOT/'src/app/desktop.py')]
    for library in (ROOT/'build').glob('*.dll'):
        command.extend(['--add-binary',f'{library};build'])
    subprocess.run(command,cwd=ROOT,check=True)
    from importlib.metadata import distributions
    import shutil
    output=ROOT/dist_dir/'AIReader'
    # Local development delivery only: never bundle research data or this
    # machine's workspace choice into a public source release.
    if args.local_workspace and (ROOT/'work/runtime/daily/db/ai_reader.db').is_file():
        import json
        (output/'reader-workspace.json').write_text(json.dumps({
            'workspace': os.path.relpath(ROOT/'work/runtime/daily', output)
        }), encoding='utf-8')
    licenses=output/'THIRD_PARTY_LICENSES'
    shutil.copytree(ROOT/'native/licenses',licenses,dirs_exist_ok=True)
    if (ROOT/'build/licenses').exists():
        shutil.copytree(ROOT/'build/licenses',licenses,dirs_exist_ok=True)
    for distribution in distributions():
        name=distribution.metadata.get('Name','unknown')
        for entry in distribution.files or []:
            if '.dist-info' in str(entry) and any(word in Path(entry).name.lower() for word in ('license','copying','notice','metadata')):
                dest=licenses/name/str(entry).split('.dist-info/',1)[-1]
                dest.parent.mkdir(parents=True,exist_ok=True)
                source=distribution.locate_file(entry)
                if source.is_file():shutil.copy2(source,dest)
    for name in ('LICENSE','README.md','README.en.md','THIRD_PARTY.md'):
        shutil.copy2(ROOT/name,output/name)
    for name in ('ARCHITECTURE.md','PRODUCT_PURPOSE.md','DOMAIN_PROFILES.md','MYSQL.md','WORKSPACE_GUIDE.md','PUBLISHING.md','RELEASE_NOTES.md','MODEL_OUTPUT.md'):
        dest=output/'docs'/name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(ROOT/'docs'/name,dest)
    shutil.copytree(ROOT/'docs/assets/readme',output/'docs/assets/readme',dirs_exist_ok=True)
    # A console build preserves redirected worker stdout; this launcher hides
    # the desktop's console without breaking the JSON Lines worker protocol.
    (output/'Start AI Reader.vbs').write_text(
        'Set s = CreateObject("WScript.Shell")\n'
        'Set f = CreateObject("Scripting.FileSystemObject")\n'
        's.Run Chr(34) & f.BuildPath(f.GetParentFolderName(WScript.ScriptFullName), "AIReader.exe") & Chr(34), 0, False\n',encoding='ascii')
    print(output)

if __name__=='__main__':
    main()
