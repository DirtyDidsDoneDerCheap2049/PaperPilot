import json, logging, os, re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _scrub_keys(text: str) -> str:
    for var in ("DEEPSEEK_API_KEY", "MINERU_API_TOKEN", "OPENAI_API_KEY",
                "SEMANTIC_SCHOLAR_API_KEY"):
        val = os.getenv(var, "")
        if val and len(val) > 8:
            text = text.replace(val, "[REDACTED]")
    return text


class ReportGenerator:
    def __init__(self, db=None):
        self.db = db

    def generate(self, workspace_root: Path, direction: dict,
                 papers: list[dict], innovations: list[dict],
                 analysis: dict, missing: list[dict]) -> Path:
        reports_dir = workspace_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = reports_dir / f"gap_analysis_{ts}.md"

        md = f"""# 空白创新点分析报告

## 1. 研究方向
{json.dumps(direction, ensure_ascii=False, indent=2)}

## 2. 检索覆盖
- 找到论文: {len(papers)}
- 有全文: {sum(1 for p in papers if p.get('retrieval_status') == 'parsed')}
- 仅元数据: {sum(1 for p in papers if p.get('retrieval_status') == 'metadata_only')}
- 提取创新画像: {len(innovations)}

## 3. 创新覆盖矩阵
{json.dumps(analysis.get('matrix', {}), ensure_ascii=False, indent=2)}

## 4. 候选空白
"""
        for i, gap in enumerate(analysis.get("gaps", []), 1):
            md += f"""
### 空白 {i}: {gap.get('name', '')}
- 描述: {gap.get('description', '')}
- 可行性: {gap.get('feasibility', '')}/5
- 新颖性: {gap.get('novelty', '')}/5
- 难度: {gap.get('difficulty', '')}/5
- 置信度: {gap.get('confidence', '')}
"""
            for nw in gap.get("nearest_works", [])[:5]:
                paper = self.db.fetchone(
                    "SELECT title, venue, year FROM papers WHERE id=?",
                    (nw.get("paper_id", ""),),
                ) if self.db else None
                if paper:
                    md += f"  - {paper['title']} ({paper.get('venue','')} {paper.get('year','')}): {nw.get('difference','')}\n"
                else:
                    md += f"  - {nw.get('paper_id','')}: {nw.get('difference','')}\n"
        if missing:
            md += "\n## 5. 缺全文论文提醒\n"
            md += "以下论文仅从摘要分析，建议手动下载全文后放入 `papers/manual/` 以提升分析置信度。\n\n"
            for m in missing[:10]:
                md += f"- **{m.get('title','')}** ({m.get('source','')}, {m.get('venue','')} {m.get('year','')})\n"
        md += "\n## 6. 推荐下一步\n"
        md += "- 对照空白列表，选择可行性最高且与自身技术栈最匹配的方向进行初步实验\n"
        md += "- 优先下载标注为缺全文的高引用论文，补充解析后重新运行分析\n"
        md += "- 如果某个空白已有\"几乎做过\"的论文，仔细阅读其方法细节确认差异\n"
        md += f"\n---\n*AI Reader v0 自动生成于 {ts}*\n"
        path.write_text(_scrub_keys(md), encoding="utf-8")
        return path
