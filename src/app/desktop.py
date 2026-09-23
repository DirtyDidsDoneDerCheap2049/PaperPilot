import argparse, sys, threading, time, logging, socket, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_reader")


def _build_app(ws_root: Path):
    from src.app.factory import build_app
    return build_app(ws_root, enable_file_logging=True)


def default_workspace():
    from src.runtime.workspace_location import default_workspace as resolve_default
    return resolve_default(Path(sys.executable).parent if getattr(sys, 'frozen', False) else None)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace")
    parser.add_argument('--worker', nargs='?')
    parser.add_argument('--db-env',type=Path)
    parser.add_argument('--storage', choices=('sqlite','mysql'), help='新工作区默认本地保存；mysql 打开数据库连接向导')
    parser.add_argument('--import-history', type=Path, help='首次将旧 SQLite 工作区复制到 --workspace；再次打开复用副本')
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        from src.runtime.worker import main as worker_main
        raise SystemExit(worker_main(args.worker))
    ws_root = Path(args.workspace or default_workspace()).resolve()
    if args.import_history:
        from src.runtime.history_import import import_history
        import_history(args.import_history,ws_root)
        os.environ['READER_STORAGE']='sqlite'
    from src.runtime.database_config import load_database_config,ready,storage_backend
    if args.storage:
        os.environ['READER_STORAGE']=args.storage
    load_database_config(ws_root,args.db_env)
    os.environ['READER_STORAGE']=storage_backend(ws_root)
    if os.environ['READER_STORAGE']=='mysql' and not ready():
        from src.app.database_setup import run_setup
        run_setup(ws_root)
        return

    try:
        app, config = _build_app(ws_root)
    except RuntimeError:
        if args.smoke_test:raise
        if os.environ['READER_STORAGE']!='mysql':raise
        from src.app.database_setup import run_setup
        run_setup(ws_root)
        return

    import webview
    host = '127.0.0.1'
    listener=socket.socket()
    listener.bind((host,0))
    port=listener.getsockname()[1]
    import uvicorn
    # Pin the mature WebSocket implementation: auto selected SansIO can race
    # WebView's close handshake during application shutdown.
    server=uvicorn.Server(uvicorn.Config(app,host=host,port=port,log_level='info',ws='websockets'))

    def run_server():
        server.run(sockets=[listener])

    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    import urllib.request
    ready=False
    for _ in range(150):
        try:
            with urllib.request.urlopen(f'http://{host}:{port}/api/health',timeout=.3) as response:
                ready=response.status==200
            if ready: break
        except Exception:
            if not t.is_alive(): break
            time.sleep(.1)
    if not ready:
        server.should_exit=True
        t.join(timeout=10)
        raise RuntimeError('桌面后台启动失败，请检查任务内核构建和工作区是否已打开')

    webview.settings['ALLOW_DOWNLOADS'] = True
    window = webview.create_window(
        title="PaperPilot",
        url=f"http://{host}:{port}/",
        width=1200, height=800, min_size=(800, 600),
    )
    smoke_passed = []
    def smoke():
        try:
            if not window.events.loaded.wait(40):
                raise RuntimeError('WebView 页面未在 40 秒内加载')
            print('DESKTOP_SMOKE',window.evaluate_js('document.title'),flush=True)
            smoke_passed.append(True)
        except Exception:
            logger.exception('DESKTOP_SMOKE_FAILED')
        finally:
            window.destroy()
    try:
        webview.start(smoke if args.smoke_test else None, storage_path=str(ws_root/'.webview'), private_mode=False)
    finally:
        server.should_exit=True
        t.join(timeout=15)
        listener.close()
    if args.smoke_test and not smoke_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
