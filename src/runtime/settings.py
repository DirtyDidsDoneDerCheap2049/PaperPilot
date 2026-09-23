"""Workspace-local settings; Windows credentials use DPAPI."""
import base64
import ctypes
import json
import os
from pathlib import Path
import tempfile

SECRET_KEYS={'deepseek_api_key':'DEEPSEEK_API_KEY','mineru_api_token':'MINERU_API_TOKEN','siliconflow_api_key':'SILICONFLOW_API_KEY','mysql_password':'MYSQL_PASSWORD'}

def atomic_write(path,text):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp,0o600)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)

def _dpapi(data,decrypt=False):
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    dest=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(dest)): raise ctypes.WinError()
    try: return ctypes.string_at(dest.pbData,dest.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(dest.pbData)

def read_secrets(workspace):
    path=Path(workspace)/'.secrets.json'
    if not path.exists(): return {}
    wrapper=json.loads(path.read_text(encoding='utf-8'))
    if wrapper['format']=='dpapi':
        if os.name!='nt': raise RuntimeError('密钥由 Windows 加密，请在当前系统重新配置 API')
        return json.loads(_dpapi(base64.b64decode(wrapper['data']),True))
    return wrapper['data']

def write_secrets(workspace,values):
    raw=json.dumps(values,ensure_ascii=False)
    wrapper={'format':'dpapi','data':base64.b64encode(_dpapi(raw.encode())).decode()} if os.name=='nt' else {'format':'local-permissions','data':values}
    atomic_write(Path(workspace)/'.secrets.json',json.dumps(wrapper))

def load_workspace_secrets(workspace):
    from dotenv import dotenv_values
    for key,value in dotenv_values(Path(workspace)/'.env').items():
        if value is not None: os.environ[key]=value
    for field,value in read_secrets(workspace).items():
        if field=='mysql_password' and 'MYSQL_PASSWORD' in os.environ:continue
        if field in SECRET_KEYS: os.environ[SECRET_KEYS[field]]=value
