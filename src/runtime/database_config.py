import json
import os
from pathlib import Path
from src.runtime.settings import read_secrets,write_secrets,atomic_write

PUBLIC_KEYS=('MYSQL_HOST','MYSQL_PORT','MYSQL_USER','MYSQL_DATABASE','MYSQL_SSL_CA')

def storage_backend(workspace):
    """Preserve existing MySQL workspaces; new workspaces need no server."""
    explicit = os.getenv('READER_STORAGE')
    if explicit:
        if explicit not in {'sqlite', 'mysql'}:
            raise ValueError('READER_STORAGE 必须是 sqlite 或 mysql')
        return explicit
    path = Path(workspace) / '.database.json'
    if path.exists():
        config = json.loads(path.read_text(encoding='utf-8'))
        if config.get('MYSQL_DATABASE'):
            return 'mysql'
    return 'sqlite'


def load_database_config(workspace,env_file=None):
    path=Path(workspace)/'.database.json'
    if path.exists():
        for key,value in json.loads(path.read_text(encoding='utf-8')).items():
            if key in PUBLIC_KEYS:os.environ[key]=str(value)
    saved=read_secrets(workspace)
    if 'mysql_password' in saved:os.environ['MYSQL_PASSWORD']=saved['mysql_password']
    if env_file:
        from dotenv import dotenv_values
        source=Path(env_file)
        if not source.is_file():raise RuntimeError('MySQL 连接文件不存在')
        os.environ['READER_STORAGE']='mysql'
        for key,value in dotenv_values(source).items():
            if key in (*PUBLIC_KEYS,'MYSQL_PASSWORD') and value is not None:os.environ[key]=value

def save_database_config(workspace,values):
    workspace=Path(workspace);workspace.mkdir(parents=True,exist_ok=True)
    secrets=read_secrets(workspace)
    secrets['mysql_password']=values.get('MYSQL_PASSWORD','')
    write_secrets(workspace,secrets)
    atomic_write(workspace/'.database.json',json.dumps({k:v for k,v in values.items() if k in PUBLIC_KEYS},ensure_ascii=False))

def ready():
    return os.getenv('READER_STORAGE')=='sqlite' or bool(os.getenv('MYSQL_USER') and os.getenv('MYSQL_DATABASE'))
