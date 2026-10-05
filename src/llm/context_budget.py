"""Conservative input budgeting and provenance-aware full/section reading."""
import asyncio
import logging
from pathlib import Path
from src.llm.provider_policy import is_deepseek

logger = logging.getLogger(__name__)


def context_window(llm):
    manual = getattr(llm, 'context_window', None)
    if manual:
        return int(manual)
    return 1048576 if is_deepseek(getattr(llm, '_base_url', 'https://api.deepseek.com')) else 128000


def input_byte_budget(llm):
    # UTF-8 bytes are a conservative upper bound, not the provider's measured
    # token usage. Reserve output, instructions, JSON escaping and a 10% margin.
    capacity = context_window(llm)
    output = getattr(llm, 'max_output_tokens', None) or 65536
    return max(1024, int(capacity * .9) - output - 16384)


async def prepare_reading(owner, ctx, paper, text, source, query):
    from src.knowledge.paper_retrieval import parent_spans
    retriever = getattr(owner, 'retriever', None)
    hits, index_error = [], None
    path = Path(source)
    if not path.is_absolute():
        path = ctx.workspace_root / path
    budget = input_byte_budget(owner.llm)
    complete = len(text.encode('utf-8')) <= budget
    if retriever and path.is_file() and complete:
        try:
            if not retriever.is_current(paper['id'],path,text):
                retriever.request_update()
        except Exception as exc:
            logger.warning('Paper index update deferred (%s)', type(exc).__name__)
            index_error = type(exc).__name__
    if retriever and path.is_file() and not complete:
        try:
            await asyncio.to_thread(retriever.index, paper, path, text)
            hits = await asyncio.to_thread(retriever.search, query, [paper['id']], 8)
        except Exception as exc:
            logger.warning('Paper vector retrieval failed (%s)', type(exc).__name__)
            index_error = type(exc).__name__
    spans = [(0, len(text))] if complete else []
    if not complete:
        candidates = [(h['start'], h['end']) for h in hits]
        if not candidates:
            # Explicit degraded lexical selection, never claim this read is full.
            import re
            terms = set(re.findall(r'\w+', query.casefold()))
            ranked = sorted(parent_spans(text), key=lambda span: -sum(t in text[span[0]:span[1]].casefold() for t in terms))
            candidates = [(a, b) for a, b, _ in ranked]
        used = 0
        for start, end in candidates:
            if any(start >= a and end <= b for a, b in spans):
                continue
            cost = len(text[start:end].encode('utf-8')) + 120
            if used + cost <= budget:
                spans.append((start, end))
                used += cost
        if not spans:
            raise ValueError('相关章节或完整表格超过当前输入预算，请提高模型上下文容量后重试')
    content = text if complete else '\n\n'.join('[原文位置 %d:%d]\n%s' % (a,b,text[a:b]) for a,b in sorted(spans))
    info = {'mode': 'full_document' if complete else 'retrieved_sections', 'input_complete': complete,
        'source_chars': len(text), 'input_chars': len(content), 'source_spans': [list(s) for s in sorted(spans)],
        'context_window': context_window(owner.llm), 'input_byte_budget': budget,
        'retrieval_locations': [{k: h[k] for k in ('section','start','end','methods','document_sha256')} for h in hits],
        'index_error': index_error}
    return content, info
