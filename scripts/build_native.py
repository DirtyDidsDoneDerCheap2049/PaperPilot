"""Build the local C++ task engine. Downloads are pinned and cached in work/."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
import zipfile
import io

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'work/native-vendor'
SOURCES = {
    'json.hpp': 'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp',
    'sqlite.zip': 'https://www.sqlite.org/2024/sqlite-amalgamation-3460100.zip',
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch-only', action='store_true')
    parser.add_argument('--mysql-client',type=Path,default=os.getenv('MYSQL_CLIENT_DIR'),help='MySQL 8 installation containing include/ and lib/')
    args = parser.parse_args()
    VENDOR.mkdir(parents=True, exist_ok=True)
    lock_path = ROOT / 'native/dependencies.json'
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    for name, url in SOURCES.items():
        path = VENDOR / name
        if not path.exists():
            with urllib.request.urlopen(url, timeout=90) as response:
                data = response.read()
            path.write_bytes(data)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if name in lock and lock[name]['sha256'] != digest:
            raise RuntimeError(f'Checksum mismatch: {name}')
        print(name, digest)
    with zipfile.ZipFile(io.BytesIO((VENDOR / 'sqlite.zip').read_bytes())) as archive:
        for name in ('sqlite3.c', 'sqlite3.h'):
            member = next(x for x in archive.namelist() if x.endswith('/' + name))
            (VENDOR / name).write_bytes(archive.read(member))
    if args.fetch_only:
        return
    import ziglang
    zig = Path(ziglang.__file__).parent / ('zig.exe' if os.name == 'nt' else 'zig')
    out = ROOT / 'build'
    out.mkdir(exist_ok=True)
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / 'work/zig-cache'))
    subprocess.run([str(zig), 'cc', '-O2', '-DSQLITE_THREADSAFE=1', '-DSQLITE_OMIT_LOAD_EXTENSION', '-c', str(VENDOR/'sqlite3.c'), '-o', str(out/'sqlite3.o')], check=True, env=env)
    binary = out / ('reader_tasks.exe' if os.name == 'nt' else 'reader_tasks')
    mysql_args=[]
    if args.mysql_client:
        client=args.mysql_client.resolve()
        mysql_args=['-DREADER_MYSQL','-I'+str(client/'include'),str(client/'lib/libmysql.lib')]
        import shutil
        license_dir=out/'licenses/mysql'
        license_dir.mkdir(parents=True,exist_ok=True)
        shutil.copy2(client/'LICENSE',license_dir/'LICENSE')
        shutil.copy2(client/'lib/libmysql.dll',out/'libmysql.dll')
        for path in (client/'bin').glob('lib*-*-x64.dll'):shutil.copy2(path,out/path.name)
        for name in ('libeay32.dll','ssleay32.dll'):
            path=client/'bin'/name
            if path.exists():shutil.copy2(path,out/name)
    subprocess.run([str(zig), 'c++', '-std=c++17', '-O2', '-I'+str(VENDOR), str(ROOT/'native/main.cpp'), str(out/'sqlite3.o'), '-o', str(binary)]+mysql_args+(['-lshell32'] if os.name=='nt' else []), check=True, env=env)
    print('Built', binary)

if __name__ == '__main__':
    main()
