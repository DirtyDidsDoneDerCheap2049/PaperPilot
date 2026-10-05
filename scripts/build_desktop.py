"""Build a Windows folder distribution; run from the pinned development environment."""
import os
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--local-workspace',action='store_true',help='Also update dist/PaperPilot.exe using the existing local workspace')
    args=parser.parse_args()
    dist_dir='dist/public-desktop'
    if os.name != 'nt':
        raise SystemExit('This release build targets Windows.')
    if not (ROOT/'build/reader_tasks.exe').is_file():
        subprocess.run([sys.executable,str(ROOT/'scripts/build_native.py')],check=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/prepare_embedding_model.py')],check=True)
    from create_icon import create_icon
    icon=create_icon(ROOT/'build/reader.ico')
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed',
             '--name','AIReader','--icon',str(icon),'--distpath',str(ROOT/dist_dir),
             '--workpath',str(ROOT/'build/pyinstaller'),'--specpath',str(ROOT/'build'),
             '--paths',str(ROOT),'--add-data',f'{ROOT / "src/ui/static"};src/ui/static',
             '--add-data',f'{ROOT / "src/knowledge/policies"};src/knowledge/policies',
             '--add-data',f'{ROOT / "build/models/paper-embedding"};resources/models/paper-embedding',
             '--add-binary',f'{ROOT / "build/reader_tasks.exe"};build',
             '--collect-submodules','src', '--collect-data','webview',
             '--collect-all','chromadb','--collect-all','onnxruntime','--collect-all','tokenizers',
             '--exclude-module','torch',
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
    # The public build stays independent of the local workspace selector.
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
    for name in ('INDEX.md','WORKSPACES.md','RELEASE_CHECKS.md','LOCAL_RETRIEVAL_BENCHMARK.md',
                 'FULLTEXT_RETRIEVAL.md','ARCHITECTURE.md','ADAPTIVE_RESEARCH.md','LIBRARY_ORGANIZATION.md','PRODUCT_PURPOSE.md','DOMAIN_PROFILES.md','MYSQL.md','WORKSPACE_GUIDE.md','PUBLISHING.md','RELEASE_NOTES.md','MODEL_OUTPUT.md','MODEL_COST.md','RUNTIME_PERFORMANCE.md'):
        dest=output/'docs'/name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(ROOT/'docs'/name,dest)
    shutil.copytree(ROOT/'docs/releases',output/'docs/releases',dirs_exist_ok=True)
    shutil.copytree(ROOT/'docs/assets/readme',output/'docs/assets/readme',dirs_exist_ok=True)
    # Keep the legacy shortcut compatible. Direct EXE startup is now windowed;
    # workers explicitly recover inherited pipes instead of requiring a console.
    (output/'Start AI Reader.vbs').write_text(
        'Set s = CreateObject("WScript.Shell")\n'
        'Set f = CreateObject("Scripting.FileSystemObject")\n'
        's.Run Chr(34) & f.BuildPath(f.GetParentFolderName(WScript.ScriptFullName), "AIReader.exe") & Chr(34), 0, False\n',encoding='ascii')
    if args.local_workspace:
        from update_local_desktop import update
        update()
    print(output)

if __name__=='__main__':
    main()
