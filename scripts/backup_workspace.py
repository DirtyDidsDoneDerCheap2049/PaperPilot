"""Copy a CLOSED workspace, including WAL files and PDFs, to a new directory."""
import argparse
from pathlib import Path
import shutil
import sqlite3

def backup(source, destination):
    source=Path(source).resolve(); destination=Path(destination).resolve()
    import os
    if os.getenv('READER_STORAGE','mysql')=='mysql':
        print('注意：这里只复制 PDF、报告和配置。MySQL 数据须另外运行 scripts/backup_mysql.py 备份。')
    if not (source/'config.yaml').is_file():
        raise ValueError('Source is not a Reader workspace')
    if destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Destination must be a new directory outside the workspace')
    lock=source/'db/tasks.db.lock'
    owner=None
    import os
    try:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            create=ctypes.windll.kernel32.CreateFileW
            create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
            create.restype=wintypes.HANDLE
            owner=create(str(lock),0x80000000,0,None,4,0x80,None)
            if owner==ctypes.c_void_p(-1).value: raise RuntimeError('Close Reader before backup')
        else:
            import fcntl
            owner=lock.open('a+b'); fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        shutil.copytree(source,destination,ignore=shutil.ignore_patterns('.webview','tasks.db.lock'))
        for database in (destination/'db').glob('*.db'):
            with sqlite3.connect(database) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise RuntimeError('Backup integrity check failed')
    finally:
        if owner is not None:
            if os.name=='nt':
                if owner != ctypes.c_void_p(-1).value:
                    ctypes.windll.kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
                    ctypes.windll.kernel32.CloseHandle(owner)
            else: owner.close()
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source');parser.add_argument('destination')
    args=parser.parse_args()
    print(backup(args.source,args.destination))
