import argparse, sys, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ai_reader")

from fastapi import FastAPI


app = FastAPI(title="AI Reader")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument('--db-env',type=Path,help='MySQL connection file; values are never printed')
    args = parser.parse_args()
    ws_root = Path(args.workspace).resolve()
    from src.runtime.database_config import load_database_config
    load_database_config(ws_root,args.db_env)

    from src.app.factory import build_app
    _, config = build_app(
        ws_root,
        app=app,
        enable_file_logging=True,
    )

    import uvicorn
    host = "127.0.0.1"
    port = args.port or config.get("server", {}).get("port", 8765)

    logger.info(f"Starting server at http://{host}:{port}, workspace={ws_root}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
