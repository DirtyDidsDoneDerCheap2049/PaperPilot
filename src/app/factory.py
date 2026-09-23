"""Shared FastAPI application factory for browser and desktop entry points."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger("ai_reader")


def _ensure_file_logging(workspace_root: Path) -> None:
    log_path = (workspace_root / ".agent_history" / "app.log").resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                if Path(handler.baseFilename).resolve() == log_path:
                    return
            except (AttributeError, OSError):
                continue
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(log_path, maxBytes=5*1024*1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    root_logger.addHandler(handler)


def build_app(workspace_root: Path, *, app=None,
              enable_file_logging: bool = False):
    """Build and initialize one AI Reader FastAPI application."""
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    workspace_root = Path(workspace_root).resolve()
    from src.runtime.settings import load_workspace_secrets
    load_workspace_secrets(workspace_root)

    from src.workspace.manager import WorkspaceManager
    if not (workspace_root / "config.yaml").exists():
        WorkspaceManager.create_workspace(workspace_root, workspace_root.name)
    workspace = WorkspaceManager(workspace_root)
    config = workspace.load_config()

    if enable_file_logging:
        _ensure_file_logging(workspace_root)

    from src.knowledge.store import open_store
    db = open_store(workspace_root)
    db.init_schema()

    from src.llm.deepseek_client import DeepSeekClient
    llm_config = config.get("llm", {})
    llm = DeepSeekClient(
        base_url=llm_config.get("base_url", "https://api.deepseek.com"),
        fast_model=llm_config.get("fast_model", "deepseek-flash"),
        reasoning_model=llm_config.get("reasoning_model", "deepseek-flash"),
        max_output_tokens=llm_config.get('max_output_tokens'),
        timeout_seconds=llm_config.get('timeout_seconds', 600),
        regular_effort=llm_config.get('regular_effort', 'max'),
        analysis_effort=llm_config.get('analysis_effort', 'max'),
        capability_profile=llm_config.get('capability_profile','auto'),
        token_parameter=llm_config.get('token_parameter','max_tokens'),
        json_output=llm_config.get('json_output','auto'),
        extra_body_params=llm_config.get('extra_body_params',{}),
    )
    from src.runtime.research_limits import research_limits
    llm.max_calls = research_limits(config)['max_model_calls']

    from src.knowledge.vector_store import VectorStore, LocalSearchStore
    from src.llm.siliconflow_embedding import siliconflow_embedding_fn
    embedding_config = config.get("embedding", {})
    embedding_fn = siliconflow_embedding_fn(embedding_config) if embedding_config.get('provider') != 'local' else None
    if embedding_fn:
        import hashlib, json
        index_key = hashlib.sha256(json.dumps(embedding_fn.get_config(), sort_keys=True).encode()).hexdigest()[:16]
        vector_store = VectorStore(workspace_root / 'db' / ('chroma-' + index_key),embedding_fn=embedding_fn)
        vector_store.mode = '外部向量检索'
    else:
        if getattr(db,'backend','sqlite')=='mysql':
            from src.knowledge.mysql_store import MySQLSearchStore
            vector_store=MySQLSearchStore(db)
        else:
            vector_store = LocalSearchStore(workspace_root / 'db/text_index.db')

    from src.knowledge.kb_manager import KBManager
    knowledge_base = KBManager(db, vector_store)
    if app is None:
        app = FastAPI(title="AI Reader")
    from src.server.websocket import manager
    from src.runtime.tasks import TaskService
    @asynccontextmanager
    async def lifespan(application):
        application.state.tasks=TaskService(workspace_root,db,manager)
        try:
            await application.state.tasks.start()
            yield
        finally:
            await application.state.tasks.close()
            await llm.close()
            if hasattr(vector_store,'close'): vector_store.close()
            db.close()
    app.router.lifespan_context=lifespan
    from src.server.local_security import LocalOnlyMiddleware
    app.add_middleware(LocalOnlyMiddleware)
    app.state.db = db
    app.state.llm = llm
    app.state.vs = vector_store
    app.state.kb = knowledge_base
    app.state.config = config
    app.state.workspace_root = workspace_root
    app.state.workspace_id = workspace.get_workspace_id()
    app.state.workspace_manager = workspace

    from src.tools.registry import ToolRegistry
    from src.tools.init_tools import register_all_tools
    registry = register_all_tools(
        ToolRegistry(db=db),
        kb_manager=knowledge_base,
    )
    app.state.registry = registry

    from src.agents.orchestrator import Orchestrator
    app.state.orchestrator = Orchestrator(
        db, llm, vector_store, knowledge_base
    )

    static_dir = Path(__file__).resolve().parent.parent / "ui" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    from src.server.routes_chat import router as chat_router
    from src.server.routes_workspace import router as workspace_router
    from src.server.routes_config import router as config_router
    from src.server.provider_settings import router as settings_router
    from src.server.routes_jobs import router as jobs_router
    from src.server.websocket import router as websocket_router
    app.include_router(chat_router)
    app.include_router(workspace_router)
    app.include_router(config_router)
    app.include_router(settings_router)
    app.include_router(jobs_router)
    app.include_router(websocket_router)
    @app.get('/api/health')
    async def health():
        db.fetchone('SELECT 1')
        await app.state.tasks.rpc('ping')
        return {'status':'ok','product':'AI Reader','task_engine':'C++','desktop':True}

    logger.info(
        "Application initialized for %s with %s tools",
        workspace_root,
        len(registry.list_tools()),
    )
    return app, config
