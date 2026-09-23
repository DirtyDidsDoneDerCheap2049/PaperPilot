"""Explicit resource limits for a multi-paper research run."""
DEFAULT_MAX_CALLS = 200
DEFAULT_TIMEOUT_MINUTES = 120
DEFAULT_FULLTEXT_PAPERS = 50


def research_limits(config):
    section = (config or {}).get('research', {})
    def number(key, default, low, high):
        try:
            return max(low, min(high, int(section.get(key, default))))
        except (TypeError, ValueError):
            return default
    return {'max_model_calls': number('max_model_calls', DEFAULT_MAX_CALLS, 1, 1000),
            'timeout_minutes': number('timeout_minutes', DEFAULT_TIMEOUT_MINUTES, 1, 480)}
