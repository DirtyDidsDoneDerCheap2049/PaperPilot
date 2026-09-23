"""Portable, scoped research exports without workspace credentials or paths."""
import hashlib
import json
import re
import zipfile
from pathlib import Path
from src.runtime.settings import read_secrets, atomic_write

def write_handoff(workspace, output, markdown, context, manifest):
    workspace=Path(workspace).resolve()
    secrets=[value for value in read_secrets(workspace).values() if isinstance(value,str) and value]
    def scrub(text):
        for secret in secrets:text=text.replace(secret,'[密钥已隐藏]')
        for base in (str(workspace).replace('\\','\\\\'),str(workspace),workspace.as_posix()):
            text=text.replace(base+'\\\\','').replace(base+'\\','').replace(base+'/','').replace(base,'[工作区]')
        text=re.sub(r'[A-Za-z]:[\\/][^\s\n"`<>]*','[本机路径已隐藏]',text)
        text=re.sub(r'\bsk-[A-Za-z0-9_-]{12,}','[密钥已隐藏]',text)
        return text
    def sanitize(value):
        if isinstance(value,dict):return {k:sanitize(v) for k,v in value.items()}
        if isinstance(value,list):return [sanitize(v) for v in value]
        return scrub(value) if isinstance(value,str) else value
    clean_md=scrub(markdown)
    context_path=output.with_suffix('.context.json')
    manifest_path=output.with_suffix('.manifest.json')
    archive_path=output.with_suffix('.zip')
    atomic_write(output,clean_md)
    atomic_write(context_path,json.dumps(sanitize(context),ensure_ascii=False,indent=2))
    manifest=sanitize(manifest)
    manifest['files']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (output,context_path)}
    atomic_write(manifest_path,json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(archive_path,'x',zipfile.ZIP_DEFLATED) as archive:
        for file in (output,context_path,manifest_path):archive.write(file,file.name)
    return {key:str(path.relative_to(workspace)).replace('\\','/') for key,path in (
        ('zip_path',archive_path),('context_path',context_path),('manifest_path',manifest_path))}
