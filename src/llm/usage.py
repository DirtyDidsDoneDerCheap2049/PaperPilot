"""Provider-reported tokens only; missing usage is not zero usage."""


def get(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_usage(value):
    if value is None:
        return None
    prompt = count(get(value, 'prompt_tokens'))
    output = count(get(value, 'completion_tokens'))
    hit = count(get(value, 'prompt_cache_hit_tokens'))
    if hit is None:
        hit = count(get(get(value, 'prompt_tokens_details'), 'cached_tokens'))
    miss = count(get(value, 'prompt_cache_miss_tokens'))
    if miss is None and prompt is not None and hit is not None and hit <= prompt:
        miss = prompt - hit
    if prompt is not None and hit is not None and (hit > prompt or (miss is not None and hit + miss != prompt)):
        hit = miss = None
    reasoning = count(get(get(value, 'completion_tokens_details'), 'reasoning_tokens'))
    if reasoning is not None and output is not None and reasoning > output:
        reasoning = None
    if prompt is None and output is None and hit is None:
        return None
    return {'prompt_tokens': prompt, 'completion_tokens': output,
            'prompt_cache_hit_tokens': hit, 'prompt_cache_miss_tokens': miss,
            'reasoning_tokens': reasoning}


def summarize_usage(records, requests, local_hits=0):
    rows = [row['usage'] for row in records if row.get('usage')]
    cached = [row for row in rows if row['prompt_cache_hit_tokens'] is not None and row['prompt_cache_miss_tokens'] is not None]
    hits = sum(row['prompt_cache_hit_tokens'] for row in cached)
    misses = sum(row['prompt_cache_miss_tokens'] for row in cached)
    def total(field):
        values = [row[field] for row in rows if row[field] is not None]
        return sum(values) if values else None
    return {'model_requests': requests, 'requests_with_usage': len(rows),
            'requests_without_usage': max(0, requests-len(rows)),
            'requests_with_cache_usage': len(cached),
            'prompt_tokens': total('prompt_tokens'), 'completion_tokens': total('completion_tokens'),
            'prompt_cache_hit_tokens': hits if cached else None,
            'prompt_cache_miss_tokens': misses if cached else None,
            'input_cache_hit_rate': hits/(hits+misses) if hits+misses else None,
            'reasoning_tokens': total('reasoning_tokens'),
            'local_evidence_cache_hits': local_hits}
