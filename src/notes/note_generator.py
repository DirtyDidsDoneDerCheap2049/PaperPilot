import logging, re, json
from pathlib import Path

logger = logging.getLogger(__name__)

def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text)[:60]
    slug = re.sub(r"\s+", "_", slug.strip())
    return slug.lower()


class NoteGenerator:
    def __init__(self, llm_client=None, db=None):
        self.llm = llm_client
        self.db = db

    def generate_paper_note(self, paper: dict, profile: dict,
                            output_root: Path) -> Path:
        venue = _safe_slug(str(paper.get("venue") or "unknown")) or "unknown"
        year = _safe_slug(str(paper.get("year") or "unknown")) or "unknown"
        area = _safe_slug(str(profile.get("research_domain") or "general")) or "general"
        slug = _safe_slug(paper.get("title", "untitled"))

        out_dir = output_root / venue / year / area
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{slug}.md"

        # Collect related paper references if DB available
        related_html = "<!-- RELATED:START -->\n"
        if self.db:
            techniques = profile.get("key_techniques", [])
            subcat = profile.get("method_subcategory", "")
            similar_query = f"{subcat} {' '.join(techniques[:3])}"
            try:
                similar = self.db.fetchall(
                    """SELECT id, title, venue, year FROM papers
                       WHERE id != ? ORDER BY citation_count DESC LIMIT 5""",
                    (paper.get("id", ""),),
                )
                for s in similar:
                    related_html += f"- [{s['title']}]({s['venue']} {s['year']})\n"
            except Exception:
                pass
        related_html += "<!-- RELATED:END -->"

        md = f"""---
title: "{paper.get('title', '')}"
paper_id: "{paper.get('id', '')}"
venue: "{venue}"
year: {year}
area: "{area}"
has_fulltext: {json.dumps(profile.get('has_fulltext', False))}
confidence: {profile.get('confidence', 0.5)}
tags: {json.dumps(profile.get('key_techniques', []))}
---

# {paper.get('title', 'Untitled')}

**会议**: {venue}
**年份**: {year}
**arXiv**: {paper.get('url', '')}
**代码**: 
**领域**: {profile.get('research_domain') or '未分类'}
**目标任务**: {profile.get('task_or_problem') or '未提取'}

## 一句话总结
{profile.get('method_summary') or profile.get('innovation_detail', '')[:200]}

## 研究背景与动机

## 方法详解

## 实验关键数据
{json.dumps(profile.get('performance', {}), ensure_ascii=False)}

## 创新点画像
- 改进环节: {profile.get('method_component', '')}
- 方法类别: {profile.get('method_category', '')}
- 具体方法: {profile.get('method_subcategory', '')}
- 基准方法: {profile.get('baseline_method', '')}

## 亮点与洞察

## 局限与展望

## 相关论文
{related_html}
"""

        path.write_text(md, encoding="utf-8")
        return path
