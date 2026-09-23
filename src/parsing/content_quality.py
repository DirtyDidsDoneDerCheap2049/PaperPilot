"""Reject unmistakable access/error pages; this does not assess scientific quality."""
import re


def unusable_fulltext_reason(text):
    content=re.sub(r'^## Page \d+\s*','',text or '',flags=re.M).strip()
    if not content:
        return '没有可提取的正文，可能需要 OCR'
    if len(content)<5000 and re.search(
        r'javascript is disabled|enable javascript to (?:proceed|continue)|'
        r'checking your browser|verify (?:that )?you are human|access denied|'
        r'just a moment\.\.\.|captcha verification',content,re.I):
        return '取得的是访问验证或错误页面，未获得论文全文'
    return ''
