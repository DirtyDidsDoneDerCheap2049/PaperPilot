import json, uuid, asyncio, logging
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field
from pathlib import Path

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get('/api/sessions/{session_id}/agent-runs')
async def session_agent_runs(session_id: str, request: Request):
    db=request.app.state.db
    rows=db.fetchall('SELECT * FROM agent_runs WHERE session_id=? ORDER BY started_at, id',(session_id,))
    for row in rows:
        for key in ('input_json','output_json'):
            try:row[key]=json.loads(row.get(key) or '{}')
            except (ValueError,TypeError):row[key]={'unparsed':row.get(key)}
    return {'runs':rows}


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=20000)
    selected_paper_ids: list[str] = Field(default_factory=list, max_length=100)
    mode: str = "analysis"
    report_path: str | None = None


class MessageEditRequest(BaseModel):
    content: str


def _safe_report_file(ws_root: Path, rel_path: str | None) -> Path:
    if not rel_path:
        raise HTTPException(400, "report_path required")
    root = ws_root.resolve()
    fp = (root / rel_path).resolve()
    try:
        fp.relative_to((root / "reports").resolve())
    except ValueError:
        raise HTTPException(403, "Only reports can be used as report chat context")
    if fp.suffix.lower() != ".md":
        raise HTTPException(400, "Only Markdown reports can be used")
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "Report not found")
    return fp


def _latest_session_report_file(db, ws_root: Path, session_id: str) -> Path | None:
    row = db.fetchone(
        """SELECT report_path FROM gap_analyses
           WHERE session_id=? AND report_path IS NOT NULL
           ORDER BY created_at DESC LIMIT 1""",
        (session_id,),
    )
    if not row or not row.get("report_path"):
        return None
    try:
        return _safe_report_file(ws_root, row["report_path"])
    except HTTPException:
        return None


def _recent_session_context(db, session_id: str, limit: int = 12, max_chars: int = 12000) -> str:
    rows = db.fetchall(
        """SELECT role, content FROM messages
           WHERE session_id=? ORDER BY created_at DESC LIMIT ?""",
        (session_id, limit),
    )
    parts = []
    total = 0
    for row in reversed(rows):
        role = row.get("role") or "unknown"
        content = (row.get("content") or "").strip()
        if not content:
            continue
        content = content[:2500]
        item = f"{role}: {content}"
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(item) > remaining:
            item = item[:remaining]
        parts.append(item)
        total += len(item)
    return "\n\n".join(parts)


def _safe_workspace_file(ws_root: Path, path_value: str | None) -> Path | None:
    if not path_value:
        return None
    root = ws_root.resolve()
    fp = Path(path_value)
    if not fp.is_absolute():
        fp = root / fp
    fp = fp.resolve()
    try:
        fp.relative_to(root)
    except ValueError:
        return None
    return fp if fp.exists() and fp.is_file() else None


def _read_text_excerpt(fp: Path, max_chars: int) -> str:
    try:
        if fp.suffix.lower() in {".md", ".txt"}:
            return fp.read_text(encoding="utf-8", errors="ignore")[:max_chars]
        if fp.suffix.lower() == ".pdf":
            try:
                import fitz
            except Exception:
                return "[PDF 未解析，当前环境无法加载 PyMuPDF 抽取文本]"
            chunks = []
            with fitz.open(str(fp)) as doc:
                for idx, page in enumerate(doc):
                    if idx >= 8 or sum(len(c) for c in chunks) >= max_chars:
                        break
                    chunks.append(page.get_text("text"))
            return "\n".join(chunks)[:max_chars]
    except Exception as exc:
        return f"[读取文件失败: {exc}]"
    return ""


def _decode_authors_json(value: str | None) -> list[str]:
    try:
        data = json.loads(value or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _selected_paper_context(db, ws_root: Path, paper_ids: list[str], max_papers: int = 8,
                            max_chars: int = 36000) -> str:
    ids = [pid for pid in dict.fromkeys(paper_ids or []) if isinstance(pid, str) and pid][:max_papers]
    if not ids:
        return ""
    parts = []
    total = 0
    for idx, paper_id in enumerate(ids, start=1):
        row = db.fetchone("SELECT * FROM papers WHERE id=?", (paper_id,))
        if not row:
            continue
        authors = ", ".join(_decode_authors_json(row.get("authors_json"))) or "Unknown authors"
        venue = " / ".join(str(v) for v in (row.get("venue"), row.get("year")) if v) or "Unknown venue"
        lines = [
            f"[{idx}] {row.get('title') or paper_id}",
            f"ID: {paper_id}",
            f"Authors: {authors}",
            f"Venue/Year: {venue}",
            f"Status: {row.get('retrieval_status') or 'metadata_only'}",
        ]
        if row.get("abstract"):
            lines.append(f"Abstract: {row.get('abstract')}")
        text = ""
        source = ""
        parsed_fp = _safe_workspace_file(ws_root, row.get("parsed_markdown_path"))
        fulltext_fp = _safe_workspace_file(ws_root, row.get("fulltext_path"))
        if parsed_fp:
            source = str(parsed_fp)
            text = _read_text_excerpt(parsed_fp, 10000)
        elif fulltext_fp:
            source = str(fulltext_fp)
            text = _read_text_excerpt(fulltext_fp, 10000)
        if source:
            lines.append(f"Local file: {source}")
        lines.append(f"Readable excerpt:\n{text or '[没有可读取的全文片段，当前仅有元数据]'}")
        item = "\n".join(lines)
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(item) > remaining:
            item = item[:remaining]
        parts.append(item)
        total += len(item)
    return "\n\n".join(parts)


async def _run_report_chat(req: ChatRequest, request: Request,
                           session_id: str, message_id: str) -> None:
    from src.server.websocket import manager
    app_state = request.app.state
    ws_root = app_state.workspace_root
    db = app_state.db
    existing = db.fetchone("SELECT id FROM sessions WHERE id=?", (session_id,))
    report_file = _safe_report_file(ws_root, req.report_path) if req.report_path else _latest_session_report_file(db, ws_root, session_id)
    report_content = report_file.read_text(encoding="utf-8")[:60000] if report_file else ""
    recent_context = _recent_session_context(db, session_id) if existing else ""
    paper_context = _selected_paper_context(db, ws_root, req.selected_paper_ids)
    title = f"只对话: {report_file.name}" if report_file else f"只对话: {req.message.strip()}"
    report_path_meta = str(report_file) if report_file else None
    selected_paper_ids = [pid for pid in dict.fromkeys(req.selected_paper_ids or []) if pid]
    if not existing:
        db.execute(
            """INSERT INTO sessions (id, workspace_id, title, status)
               VALUES (?,?,?,?)""",
            (session_id, app_state.workspace_id, title[:80], "busy"),
        )
    db.execute(
        """INSERT OR IGNORE INTO messages (id, session_id, role, content, metadata_json)
           VALUES (?,?,?,?,?)""",
         (message_id, session_id, "user", req.message,
         json.dumps({"mode": "report_chat", "report_path": report_path_meta,
                     "selected_paper_ids": selected_paper_ids}, ensure_ascii=False)),
    )

    messages = [
        {"role": "system", "content": (
            "你是当前研究方向的结果讨论助手。用户正在当前会话里继续对话，不希望生成新报告。"
            "只能基于同一会话历史、已有报告内容（如果提供）、用户选择/上传的论文文件上下文、用户问题和明确标注的不确定性回答；不要启动新检索，不要声称生成了新报告。"
            "如果报告证据不足，要直接指出，并建议用户重新运行分析或补全文。回答使用中文，结论先行。"
            "涉及本工具操作时，只能说明已知入口：输入区 PDF 按钮或论文库上传论文；"
            "只对话模式不检索、不生成报告。不要编造粘贴链接解析、DOI提交或‘无全文继续’等按钮。"
        )},
        {"role": "user", "content": (
            f"报告文件：{report_file.name if report_file else '无，当前仅使用会话上下文'}\n\n"
            f"同一会话的最近上下文（可能为空）：\n{recent_context or '无'}\n\n"
            f"报告内容（可能为空）：\n{report_content or '无'}\n\n"
            f"用户选择/上传的论文文件上下文（可能为空）：\n{paper_context or '无'}\n\n"
            f"当前用户问题：{req.message}"
        )},
    ]

    assistant_message_id = str(uuid.uuid4())
    parts = []
    try:
        async for delta in app_state.llm.chat_stream(messages):
            parts.append(delta)
            await manager.broadcast(session_id, {
                "type": "chat_delta", "session_id": session_id,
                "content": delta,
            })
        answer = "".join(parts)
    except asyncio.CancelledError:
        db.execute("UPDATE sessions SET status='idle', updated_at=datetime('now') WHERE id=?", (session_id,))
        await manager.broadcast(session_id, {
            "type": "chat_failed", "session_id": session_id, "error": "任务已取消",
        })
        raise
    except Exception as e:
        logger.exception("report_chat streaming failed")
        db.execute("UPDATE sessions SET status='idle', updated_at=datetime('now') WHERE id=?", (session_id,))
        await manager.broadcast(session_id, {
            "type": "chat_failed", "session_id": session_id, "error": str(e),
        })
        raise

    db.execute(
        """INSERT INTO messages (id, session_id, role, content, metadata_json)
           VALUES (?,?,?,?,?)""",
        (assistant_message_id, session_id, "assistant", answer,
         json.dumps({"mode": "report_chat", "report_path": report_path_meta,
                     "selected_paper_ids": selected_paper_ids}, ensure_ascii=False)),
    )
    db.execute("UPDATE sessions SET status='idle', updated_at=datetime('now') WHERE id=?", (session_id,))
    await manager.broadcast(session_id, {
        "type": "chat_completed", "session_id": session_id,
        "message_id": message_id, "assistant_message_id": assistant_message_id,
        "content": answer,
    })


@router.post("/api/chat", status_code=202)
async def chat(req: ChatRequest, request: Request):
    from src.server.routes_jobs import submit_chat
    return await submit_chat(req, request)


@router.post("/api/tasks/{session_id}/cancel")
async def cancel_task(session_id: str, request: Request):
    from src.server.routes_jobs import service, call
    job = await service(request).active_session(session_id)
    if not job:
        return {"status": "not_running", "session_id": session_id}
    result = await call(request, "cancel", id=job["id"])
    return {"status": result["status"], "session_id": session_id}


@router.get("/api/sessions/{session_id}/latest-report")
async def latest_session_report(session_id: str, request: Request):
    """Resolve the newest report file of a session.

    Needed by legacy sessions whose assistant message never stored the report
    path, so their collapsed report card can still offer an "open report" link.
    """
    db = request.app.state.db
    ws_root = Path(request.app.state.workspace_root)
    fp = _latest_session_report_file(db, ws_root, session_id)
    if not fp:
        raise HTTPException(404, "No report found for this session")
    try:
        rel = str(fp.resolve().relative_to(ws_root.resolve())).replace("\\", "/")
    except Exception:
        rel = str(fp)
    return {"status": "ok", "session_id": session_id, "path": rel}


@router.get("/api/sessions")
async def list_sessions(request: Request, q: str = ""):
    db = request.app.state.db
    if q:
        rows = db.fetchall(
            "SELECT * FROM sessions WHERE title LIKE ? ORDER BY created_at DESC LIMIT 50",
            (f"%{q}%",),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT 50"
        )
    return {"sessions": rows}


@router.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str, request: Request):
    db = request.app.state.db
    rows = db.fetchall(
        "SELECT messages.*, rowid AS rownum FROM messages WHERE session_id=? ORDER BY created_at, rowid",
        (session_id,),
    )
    return {"messages": rows}


def _message_turn_ids(db, message_id: str) -> tuple[str, list[str]]:
    target = db.fetchone("SELECT id, session_id FROM messages WHERE id=?", (message_id,))
    if not target:
        raise HTTPException(404, "Message not found")
    rows = db.fetchall(
        "SELECT rowid AS rownum, id, role FROM messages WHERE session_id=? ORDER BY created_at, rowid",
        (target["session_id"],),
    )
    idx = next((i for i, row in enumerate(rows) if row["id"] == message_id), -1)
    if idx < 0:
        raise HTTPException(404, "Message not found")
    role = rows[idx].get("role")
    if role == "user":
        start = idx
    elif role == "assistant":
        start = idx
        while start > 0 and rows[start].get("role") != "user":
            start -= 1
        if rows[start].get("role") != "user":
            start = idx
    else:
        start = idx
    end = start + 1
    while end < len(rows) and rows[end].get("role") != "user":
        end += 1
    return target["session_id"], [row["id"] for row in rows[start:end]]


@router.patch("/api/messages/{message_id}")
async def edit_message(message_id: str, body: MessageEditRequest, request: Request):
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "content required")
    db = request.app.state.db
    row = db.fetchone("SELECT id, session_id, role FROM messages WHERE id=?", (message_id,))
    if not row:
        raise HTTPException(404, "Message not found")
    if row.get("role") not in {"user", "assistant"}:
        raise HTTPException(400, "Only chat messages can be edited")
    tasks = getattr(request.app.state, 'tasks', None)
    if tasks and await tasks.active_session(row['session_id']):
        raise HTTPException(409, '请先取消或等待当前任务结束')
    with db.transaction():
        db.execute("UPDATE messages SET content=? WHERE id=?", (content, message_id))
        db.execute("UPDATE sessions SET updated_at=datetime('now') WHERE id=?", (row["session_id"],))
    updated = db.fetchone("SELECT * FROM messages WHERE id=?", (message_id,))
    return {"status": "updated", "message": updated}


@router.delete("/api/messages/{message_id}/turn")
async def delete_message_turn(message_id: str, request: Request):
    db = request.app.state.db
    session_id, ids = _message_turn_ids(db, message_id)
    tasks = getattr(request.app.state, 'tasks', None)
    if tasks and await tasks.active_session(session_id):
        raise HTTPException(409, '请先取消或等待当前任务结束')
    with db.transaction():
        for mid in ids:
            db.execute("DELETE FROM messages WHERE id=?", (mid,))
        db.execute("UPDATE sessions SET updated_at=datetime('now') WHERE id=?", (session_id,))
    return {"status": "deleted", "session_id": session_id, "deleted_message_ids": ids}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    from src.server.routes_jobs import service
    if await service(request).active_session(session_id):
        raise HTTPException(409, "请先取消或等待当前任务结束")
    db = request.app.state.db
    if not db.fetchone("SELECT id FROM sessions WHERE id=?", (session_id,)):
        raise HTTPException(404, "Session not found")
    with db.transaction():
        db.execute("DELETE FROM tool_calls WHERE agent_run_id IN (SELECT id FROM agent_runs WHERE session_id=?)", (session_id,))
        for table in ("agent_runs", "messages", "gap_analyses"):
            db.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
        db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    return {"status": "deleted", "session_id": session_id}


@router.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, request: Request):
    from pydantic import BaseModel
    class RenameRequest(BaseModel):
        title: str
    body = await request.json()
    title = body.get("title", "")
    if not title:
        raise HTTPException(400, "title required")
    db = request.app.state.db
    existing = db.fetchone("SELECT id FROM sessions WHERE id=?", (session_id,))
    if not existing:
        raise HTTPException(404, "Session not found")
    db.execute("UPDATE sessions SET title=?, updated_at=datetime('now') WHERE id=?", (title, session_id))
    return {"status": "updated", "session_id": session_id, "title": title}
