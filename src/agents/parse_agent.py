import logging, uuid, asyncio, re
from pathlib import Path
from src.agents.base import BaseAgent, AgentContext, AgentResult
from src.parsing.content_quality import unusable_fulltext_reason

logger = logging.getLogger(__name__)


class ParseAgent(BaseAgent):
    name = "ParseAgent"

    def __init__(self, db=None, progress=None):
        self.db = db
        self.progress = progress

    async def run(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        papers = input_data.get('papers', [])
        chosen = self._prioritized_papers(papers, input_data.get('deep_parse_top_k', 50), ctx.workspace_root)
        selected = {p.get('id') for p in chosen}
        data = {'parsed': [], 'metadata_only': [], 'failed': [],
                'deferred': [p.get('id') for p in papers if p.get('id') not in selected]}
        for index, paper in enumerate(chosen):
            detail = {'paper_id': paper.get('id'), 'title': paper.get('title', ''),
                      'completed': index, 'total': len(chosen)}
            if self.progress:
                await self.progress(detail)
            result = await self._run_batch(ctx, {'papers': [paper], 'deep_parse_top_k': 1})
            for key in ('parsed', 'metadata_only', 'failed'):
                data[key].extend(result.data.get(key, []))
            if self.progress:
                await self.progress(dict(detail, completed=index+1))
        return AgentResult(status='completed', data=data)

    async def _run_batch(self, ctx: AgentContext, input_data: dict) -> AgentResult:
        papers = input_data.get("papers", [])
        top_k = input_data.get("deep_parse_top_k", 10)
        ws_root = ctx.workspace_root
        parsed_dir = ws_root / "papers" / "parsed"
        config = ctx.config

        mineru_cfg = config.get("mineru", {})
        model_ver = mineru_cfg.get("model_version", "vlm")
        lang = mineru_cfg.get("language", "en")
        poll_int = mineru_cfg.get("poll_interval_seconds", 3)
        poll_timeout = mineru_cfg.get("poll_timeout_seconds", 600)

        parsed = []
        metadata_only = []
        failed = []

        for p in self._prioritized_papers(papers, top_k, ws_root):
            paper_id = p.get("id", str(uuid.uuid4()))
            pdf_url = self._infer_pdf_url(p)
            safe_id = re.sub(r'[^A-Za-z0-9._-]+','_',paper_id)
            out_dir = parsed_dir / safe_id
            cached = self._resolve_local_pdf(p.get('parsed_markdown_path'), ws_root)
            if cached and cached.is_file() and cached.stat().st_size:
                reason=unusable_fulltext_reason(cached.read_text(encoding='utf-8'))
                if not reason:
                    p['retrieval_status'] = 'parsed'
                    parsed.append(paper_id)
                    continue
                p['parsed_markdown_path']=None
                if self.db:
                    self.db.execute("UPDATE papers SET parsed_markdown_path=NULL,retrieval_status='missing_fulltext',missing_reason=? WHERE id=?",(reason,paper_id))

            # Try MinerU first
            try:
                if not mineru_cfg.get('enabled',False):
                    raise RuntimeError('外部解析已关闭，使用本地 PDF 解析')
                from src.parsing.mineru_client import MinerUClient
                token = ctx.config.get("mineru", {}).get("token_env")
                import os
                mineru_token = os.getenv(token, "") if token else ""
                mineru = MinerUClient(
                    token=mineru_token or None,
                    model=model_ver,
                    poll_interval=poll_int,
                    poll_timeout=poll_timeout,
                )
                if pdf_url and mineru.token:
                    p["retrieval_status"] = "downloaded"
                    job_id = str(uuid.uuid4())
                    self._write_parse_job(job_id, paper_id, "mineru_precise", "running")
                    task = await mineru.parse_url(pdf_url, paper_id)
                    self._write_parse_job(job_id, paper_id, "mineru_precise", task.state,
                                          task_id=task.task_id)
                    task = await mineru.poll_task(task)
                    self._write_parse_job(job_id, paper_id, "mineru_precise", task.state,
                                          task_id=task.task_id,
                                          err_msg=task.err_msg)
                    if task.state == "done":
                        md_path = await mineru.download_markdown(task, out_dir)
                        reason=unusable_fulltext_reason(md_path.read_text(encoding='utf-8'))
                        if reason: raise ValueError(reason)
                        p["parsed_markdown_path"] = str(md_path)
                        p["retrieval_status"] = "parsed"
                        self._update_paper_parse_state(paper_id, "parsed", markdown_path=md_path)
                        self._write_parse_job(job_id, paper_id, "mineru_precise", "done",
                                              task_id=task.task_id)
                        parsed.append(paper_id)
                        continue
                if pdf_url:
                    job_id = str(uuid.uuid4())
                    self._write_parse_job(job_id, paper_id, "mineru_agent", "running")
                    task = await mineru.parse_agent_url(pdf_url, paper_id)
                    self._write_parse_job(job_id, paper_id, "mineru_agent", task.state,
                                          task_id=task.task_id)
                    task = await mineru.poll_agent(task)
                    self._write_parse_job(job_id, paper_id, "mineru_agent", task.state,
                                          task_id=task.task_id,
                                          err_msg=task.err_msg)
                    if task.state == "done" and task.markdown_url:
                        md_path = await self._download_markdown_url(task.markdown_url, out_dir)
                        reason=unusable_fulltext_reason(md_path.read_text(encoding='utf-8'))
                        if reason: raise ValueError(reason)
                        p["parsed_markdown_path"] = str(md_path)
                        p["retrieval_status"] = "parsed"
                        self._update_paper_parse_state(paper_id, "parsed", markdown_path=md_path)
                        self._write_parse_job(job_id, paper_id, "mineru_agent", "done",
                                              task_id=task.task_id)
                        parsed.append(paper_id)
                        continue
                local_pdf = self._resolve_local_pdf(p.get("local_pdf_path") or p.get("fulltext_path"), ws_root)
                if local_pdf and mineru.token and local_pdf.exists():
                    job_id = str(uuid.uuid4())
                    self._write_parse_job(job_id, paper_id, "mineru_batch", "running")
                    task = await mineru.parse_local_file(local_pdf, paper_id)
                    self._write_parse_job(job_id, paper_id, "mineru_batch", task.state,
                                          task_id=task.task_id, batch_id=task.batch_id)
                    task = await mineru.poll_batch(task)
                    self._write_parse_job(job_id, paper_id, "mineru_batch", task.state,
                                          task_id=task.task_id, batch_id=task.batch_id,
                                          err_msg=task.err_msg)
                    if task.state == "done":
                        md_path = await mineru.download_markdown(task, out_dir)
                        reason=unusable_fulltext_reason(md_path.read_text(encoding='utf-8'))
                        if reason: raise ValueError(reason)
                        p["parsed_markdown_path"] = str(md_path)
                        p["retrieval_status"] = "parsed"
                        self._update_paper_parse_state(paper_id, "parsed", markdown_path=md_path, local_pdf_path=local_pdf, workspace_root=ws_root)
                        self._write_parse_job(job_id, paper_id, "mineru_batch", "done",
                                              task_id=task.task_id, batch_id=task.batch_id)
                        parsed.append(paper_id)
                        continue
            except Exception as e:
                logger.warning(f"MinerU unavailable for {paper_id}: {e}")

            # PyMuPDF fallback
            job_id = str(uuid.uuid4())
            self._write_parse_job(job_id, paper_id, "pymupdf", "running")
            try:
                pdf_path = self._resolve_local_pdf(p.get("local_pdf_path") or p.get("fulltext_path"), ws_root)
                if not pdf_path and pdf_url:
                    import httpx
                    dl_dir = ws_root / "papers" / "arxiv"
                    dl_dir.mkdir(parents=True, exist_ok=True)
                    resp = httpx.get(pdf_url, timeout=60, follow_redirects=True)
                    if resp.status_code == 200:
                        pdf_path = dl_dir / f"{safe_id}.pdf"
                        pdf_path.write_bytes(resp.content)
                        p["local_pdf_path"] = str(pdf_path)
                if pdf_path and Path(pdf_path).exists():
                    from src.parsing.pymupdf_parser import extract_markdown_like_text
                    out_dir.mkdir(parents=True, exist_ok=True)
                    md_content = extract_markdown_like_text(Path(pdf_path))
                    reason=unusable_fulltext_reason(md_content)
                    if reason: raise ValueError(reason)
                    md_path = out_dir / "full.md"
                    md_path.write_text(md_content, encoding="utf-8")
                    p["parsed_markdown_path"] = str(md_path)
                    p["retrieval_status"] = "parsed"
                    self._update_paper_parse_state(paper_id, "parsed", markdown_path=md_path, local_pdf_path=pdf_path, workspace_root=ws_root)
                    self._write_parse_job(job_id, paper_id, "pymupdf", "done")
                    parsed.append(paper_id)
                else:
                    p["retrieval_status"] = "metadata_only"
                    self._update_paper_parse_state(paper_id, "metadata_only")
                    self._write_parse_job(job_id, paper_id, "pymupdf", "metadata_only",
                                          err_msg="no_pdf_available")
                    metadata_only.append(paper_id)
            except Exception as e:
                logger.error(f"PyMuPDF fallback failed for {paper_id}: {e}")
                p["retrieval_status"] = "parse_failed"
                self._update_paper_parse_state(paper_id, "parse_failed", missing_reason=str(e))
                self._write_parse_job(job_id, paper_id, "pymupdf", "failed", err_msg=str(e))
                failed.append({"paper_id": paper_id, "reason": str(e)})

        return AgentResult(status="completed", data={
            "parsed": parsed,
            "metadata_only": metadata_only,
            "failed": failed,
        })

    def _resolve_local_pdf(self, value, ws_root: Path) -> Path | None:
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = ws_root / path
        path=path.resolve()
        return path if path.is_relative_to(ws_root.resolve()) else None

    def _prioritized_papers(self, papers: list[dict], limit: int, ws_root: Path) -> list[dict]:
        def score(p: dict) -> int:
            value = 0
            if p.get("source") == "selected_library":
                value += 8
            local_pdf = self._resolve_local_pdf(p.get("local_pdf_path") or p.get("fulltext_path"), ws_root)
            if local_pdf and local_pdf.exists():
                value += 20
            if self._infer_pdf_url(p):
                value += 12
            if p.get("retrieval_status") == "parsed":
                value -= 50
            return value

        indexed = list(enumerate(papers or []))
        indexed.sort(key=lambda item: (-score(item[1]), item[0]))
        return [p for _, p in indexed[:max(0, limit)]]

    def _infer_pdf_url(self, paper: dict) -> str | None:
        url = paper.get("open_access_pdf_url") or paper.get("pdf_url") or ""
        if not url:
            url = paper.get("url") or ""
        url = str(url or "").strip()
        if not url:
            arxiv_id = paper.get("arxiv_id")
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else None
        low = url.lower()
        blocked = ("ieeexplore.ieee.org", "sciencedirect.com", "elsevier.com", "dl.acm.org")
        if any(host in low for host in blocked):
            return None
        if "arxiv.org/abs/" in low:
            arxiv_id = url.rstrip("/").split("/")[-1]
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        if "arxiv.org/pdf/" in low:
            return url
        if low.endswith(".pdf") or "/content/pdf/" in low:
            return url
        return None

    def _update_paper_parse_state(self, paper_id: str, status: str, markdown_path: Path | None = None,
                                  local_pdf_path: Path | None = None, missing_reason: str | None = None,
                                  workspace_root: Path | None = None):
        if not self.db:
            return
        try:
            existing = self.db.fetchone("SELECT retrieval_status FROM papers WHERE id=?", (paper_id,))
            if status == "parsed":
                self.db.execute(
                    """UPDATE papers SET retrieval_status='parsed', missing_reason=NULL,
                       parsed_markdown_path=COALESCE(?, parsed_markdown_path),
                       fulltext_path=COALESCE(?, fulltext_path),
                       updated_at=datetime('now') WHERE id=?""",
                    (str(markdown_path) if markdown_path else None,
                     self._stored_path(local_pdf_path, workspace_root),
                     paper_id),
                )
            elif status == "metadata_only":
                current = (existing or {}).get("retrieval_status")
                if current not in ("downloaded", "parsed"):
                    self.db.execute(
                        """UPDATE papers SET retrieval_status='metadata_only',
                           missing_reason=COALESCE(missing_reason, 'no_pdf_available'),
                           updated_at=datetime('now') WHERE id=?""",
                        (paper_id,),
                    )
            elif status == "parse_failed":
                current = (existing or {}).get("retrieval_status")
                if current != "parsed":
                    next_status = "downloaded" if current == "downloaded" else "parse_failed"
                    self.db.execute(
                        """UPDATE papers SET retrieval_status=?, missing_reason=?,
                           updated_at=datetime('now') WHERE id=?""",
                        (next_status, missing_reason, paper_id),
                    )
        except Exception as e:
            logger.warning(f"Failed to update paper parse state for {paper_id}: {e}")

    def _stored_path(self, path: Path | None, workspace_root: Path | None) -> str | None:
        if not path:
            return None
        if workspace_root:
            try:
                return str(path.resolve().relative_to(workspace_root.resolve())).replace("\\", "/")
            except Exception:
                pass
        return str(path)

    async def _download_markdown_url(self, markdown_url: str, output_dir: Path) -> Path:
        import httpx
        output_dir.mkdir(parents=True, exist_ok=True)
        md_path = output_dir / "full.md"
        async with httpx.AsyncClient(timeout=120) as cli:
            resp = await cli.get(markdown_url)
            resp.raise_for_status()
            md_path.write_text(resp.text, encoding="utf-8")
        return md_path

    def _write_parse_job(self, job_id, paper_id, parser, state, **kwargs):
        if not self.db:
            return
        try:
            existing = self.db.fetchone(
                "SELECT id FROM parse_jobs WHERE id=?", (job_id,)
            )
            if existing:
                self.db.execute(
                    """UPDATE parse_jobs SET state=?, task_id=?, batch_id=?,
                       err_msg=?, updated_at=datetime('now') WHERE id=?""",
                    (state, kwargs.get("task_id"), kwargs.get("batch_id"),
                     kwargs.get("err_msg"), job_id),
                )
            else:
                self.db.execute(
                    """INSERT INTO parse_jobs (id, paper_id, parser, task_id,
                       batch_id, state, err_msg)
                       VALUES (?,?,?,?,?,?,?)""",
                    (job_id, paper_id, parser,
                     kwargs.get("task_id"), kwargs.get("batch_id"),
                     state, kwargs.get("err_msg")),
                )
        except Exception as e:
            logger.warning(f"Failed to write parse_job: {e}")
