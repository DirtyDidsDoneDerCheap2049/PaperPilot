import logging, os

logger = logging.getLogger(__name__)


async def mineru_parse_pdf(pdf_url: str, paper_id: str) -> dict:
    """Parse a PDF via MinerU API. Falls back gracefully."""
    try:
        from src.parsing.mineru_client import MinerUClient
        client = MinerUClient()
        if not client.token:
            return {"status": "no_token", "message": "MinerU token not configured"}
        task = await client.parse_url(pdf_url, paper_id)
        task = await client.poll_task(task)
        return {"status": task.state, "task_id": task.task_id,
                "full_zip_url": task.full_zip_url, "err_msg": task.err_msg}
    except Exception as e:
        logger.warning(f"MinerU parse failed: {e}")
        return {"status": "failed", "error": str(e)}
