"""A reading edition of the verified report; no extra model calls or new claims."""
import re
from src.analysis.report_text import readable_text


def reading_edition(markdown, direction, analysis, papers, innovations, missing):
    sections = re.split(r'(?m)(?=^## )', markdown)
    def numbered(n):
        return next((s for s in sections if re.match(rf'^## {n}\. ', s)), '')
    def without_number(section):
        section = re.sub(r'(?m)^(#{2,3}) \d+(?:\.\d+)?\.? ', r'\1 ', section)
        return re.sub(r'第 \d+ 节', '对应章节', section)
    question = str(direction.get('user_request') or direction.get('research_question') or '本轮研究问题见方向说明').strip()
    gaps = analysis.get('gaps') or []
    provisional = analysis.get('provisional_gaps') or []
    failed = sum(bool(p.get('error')) for p in innovations)
    unfinished = sum(p.get('status') in {'not_analyzed','extraction_failed','unassessed'} for p in analysis.get('paper_processing', []))
    summary = ('本轮保留了 '+str(len(gaps))+' 个有证据支持的候选，仍需核对原文与实验。') if gaps else '本轮尚不能确认可成立的研究空白。已有候选需要补证据；这不代表该方向没有可研究的问题。'
    lines = ['# 研究方向分析', '', '## 研究问题', '', question, '', '## 结论先行', '', summary, '',
             f'- 待验证想法：{len(provisional)} 个；未采纳：{len(analysis.get("rejected_gaps") or [])} 个。',
             f'- 本轮纳入 {len(papers)} 条资料；抽取失败 {failed} 条，尚未完成分析或审计 {unfinished} 篇。',
             f'- 缺全文 {len(missing)} 篇。判断范围仅限本轮取得的材料。', '']
    if (analysis.get('critic') or {}).get('skipped'):
        lines += ['本轮没有正式候选，未进行额外模型复核。', '']
    warnings=(analysis.get('reliability') or {}).get('warnings') or []
    if warnings:
        lines += ['### 本轮限制', '', readable_text(warnings), '']
    lines += ['## 已有研究与证据', '', readable_text(analysis.get('coverage_summary')) or '本轮未形成可靠综合判断，请先查看候选缺少的证据。', '']
    for title, key in [('需要注意的反证','negative_evidence'), ('可以继续核查的角度','alternative_angles')]:
        if analysis.get(key):lines += ['### '+title, '', readable_text(analysis[key]), '']
    lines += [without_number(numbered(4)), without_number(numbered(5)), without_number(numbered(7))]
    # Keep all detailed provenance, with one place for the full original record.
    lines += ['## 材料与审计附录', '', '以下清单用于逐篇核对来源、处理失败和证据。', '',
              without_number(numbered(1)), without_number(numbered(2)), without_number(numbered(3)),
              without_number(numbered(6)), without_number(numbered(8))]
    lines += [s for s in sections if s.startswith('## 附录：')]
    return '\n'.join(lines).replace('AI Reader 自动生成于', 'PaperPilot 生成于')
