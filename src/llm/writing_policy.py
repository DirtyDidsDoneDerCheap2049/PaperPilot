"""Bundled Naturawrite policy, used for reader reports rather than extraction JSON."""
import hashlib
from functools import lru_cache
from pathlib import Path

POLICY_ROOT = Path(__file__).resolve().parents[1] / 'knowledge/policies/naturawrite'


@lru_cache(maxsize=1)
def report_writing_policy():
    try:
        text = (POLICY_ROOT / 'SKILL.md').read_text(encoding='utf-8')
    except OSError as exc:
        raise ValueError('自然写作规则缺失，请更新完整软件包；本次未改写报告') from exc
    if not text.strip():
        raise ValueError('自然写作规则为空，本次未改写报告')
    return {'name': 'naturawrite', 'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'system': '\n\n以下为随程序安装的自然写作 skill，仅约束成稿表达，不能改变事实、引用或证据状态。\n' + text}
