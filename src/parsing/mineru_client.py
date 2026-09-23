import os, asyncio, io, zipfile, logging
from pathlib import Path
from dataclasses import dataclass
import httpx

logger = logging.getLogger(__name__)
BASE = "https://mineru.net"


@dataclass
class ParseTask:
    paper_id: str = ""
    parser: str = "mineru_precise"
    task_id: str = ""
    batch_id: str | None = None
    state: str = "pending"
    full_zip_url: str | None = None
    markdown_url: str | None = None
    err_msg: str | None = None


class MinerUClient:
    def __init__(self, token: str | None = None, model: str = "vlm",
                 poll_interval: int = 3, poll_timeout: int = 600):
        self.token = token or os.getenv("MINERU_API_TOKEN", "")
        self.model = model
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    @property
    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    async def parse_url(self, pdf_url: str, paper_id: str) -> ParseTask:
        async with httpx.AsyncClient(timeout=30) as cli:
            resp = await cli.post(
                f"{BASE}/api/v4/extract/task",
                headers=self._headers,
                json={
                    "url": pdf_url,
                    "model_version": self.model,
                    "language": "en",
                    "enable_formula": True,
                    "enable_table": True,
                },
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"MinerU submit failed: {data}")
            task_id = data["data"]["task_id"]
            return ParseTask(paper_id=paper_id, parser="mineru_precise",
                             task_id=task_id)

    async def poll_task(self, task: ParseTask) -> ParseTask:
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < self.poll_timeout:
            async with httpx.AsyncClient(timeout=30) as cli:
                resp = await cli.get(
                    f"{BASE}/api/v4/extract/task/{task.task_id}",
                    headers=self._headers,
                )
                data = resp.json().get("data", {})
                state = data.get("state", "failed")
                if state == "done":
                    task.state = "done"
                    task.full_zip_url = data.get("full_zip_url")
                    return task
                elif state == "failed":
                    task.state = "failed"
                    task.err_msg = data.get("err_msg", "unknown")
                    return task
                task.state = state
            await asyncio.sleep(self.poll_interval)
        task.state = "timeout"
        task.err_msg = "Polling timeout"
        return task

    async def download_markdown(self, task: ParseTask,
                                output_dir: Path) -> Path:
        if not task.full_zip_url:
            raise ValueError("No full_zip_url")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=120) as cli:
            resp = await cli.get(task.full_zip_url)
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(output_dir)
        md_files = list(output_dir.rglob("full.md"))
        if md_files:
            return md_files[0]
        md_files = list(output_dir.rglob("*.md"))
        if md_files:
            return md_files[0]
        raise FileNotFoundError("No markdown file in extracted zip")

    async def parse_local_file(self, file_path: Path,
                               paper_id: str) -> ParseTask:
        """Submit a local file for batch parsing."""
        async with httpx.AsyncClient(timeout=30) as cli:
            resp = await cli.post(
                f"{BASE}/api/v4/file-urls/batch",
                headers=self._headers,
                json={
                    "files": [{"name": file_path.name}],
                    "model_version": self.model,
                    "language": "en",
                    "enable_formula": True,
                    "enable_table": True,
                },
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"MinerU batch submit failed: {data}")
            batch_id = data["data"]["batch_id"]
            upload_url = data["data"]["file_urls"][0]
            with open(file_path, "rb") as f:
                put_resp = httpx.put(upload_url, data=f.read())
                if put_resp.status_code not in (200, 201):
                    raise RuntimeError(f"Upload failed: {put_resp.status_code}")
            return ParseTask(paper_id=paper_id, parser="mineru_precise",
                             batch_id=batch_id, task_id=batch_id)

    async def poll_batch(self, task: ParseTask) -> ParseTask:
        """Poll batch parsing until done or timeout."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < self.poll_timeout:
            async with httpx.AsyncClient(timeout=30) as cli:
                resp = await cli.get(
                    f"{BASE}/api/v4/extract-results/batch/{task.batch_id}",
                    headers=self._headers,
                )
                data = resp.json().get("data", {})
                results = data.get("extract_result", [])
                if results:
                    state = results[0].get("state", "failed")
                    if state == "done":
                        task.state = "done"
                        task.full_zip_url = results[0].get("full_zip_url")
                        return task
                    elif state == "failed":
                        task.state = "failed"
                        task.err_msg = results[0].get("err_msg", "unknown")
                        return task
            await asyncio.sleep(self.poll_interval)
        task.state = "timeout"
        task.err_msg = "Polling timeout"
        return task

    async def parse_agent_url(self, pdf_url: str,
                              paper_id: str) -> ParseTask:
        """Submit via agent lightweight API (no token)."""
        async with httpx.AsyncClient(timeout=30) as cli:
            resp = await cli.post(
                f"{BASE}/api/v1/agent/parse/url",
                json={"url": pdf_url, "language": "en"},
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Agent parse submit failed: {data}")
            task_id = data["data"]["task_id"]
            return ParseTask(paper_id=paper_id, parser="mineru_agent",
                             task_id=task_id)

    async def poll_agent(self, task: ParseTask) -> ParseTask:
        """Poll agent lightweight parse until done or timeout."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < self.poll_timeout:
            async with httpx.AsyncClient(timeout=30) as cli:
                resp = await cli.get(
                    f"{BASE}/api/v1/agent/parse/{task.task_id}"
                )
                data = resp.json().get("data", {})
                state = data.get("state", "failed")
                if state == "done":
                    task.state = "done"
                    task.markdown_url = data.get("markdown_url")
                    return task
                elif state == "failed":
                    task.state = "failed"
                    task.err_msg = data.get("err_msg", "unknown")
                    return task
            await asyncio.sleep(self.poll_interval)
        task.state = "timeout"
        task.err_msg = "Polling timeout"
        return task
