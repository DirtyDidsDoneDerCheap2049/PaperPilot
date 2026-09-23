from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/config")
async def get_config(request: Request):
    """Return non-sensitive config for UI display."""
    config = request.app.state.config
    safe = {
        "llm": {"provider": config.get("llm", {}).get("provider", ""),
                "fast_model": config.get("llm", {}).get("fast_model", ""),
                "reasoning_model": config.get("llm", {}).get("reasoning_model", "")},
        "mineru": {"enabled": config.get("mineru", {}).get("enabled", False)},
        "search": {"max_rounds": config.get("search", {}).get("max_rounds", 3),
                   "top_k_per_round": config.get("search", {}).get("top_k_per_round", 20)},
        "agents": {"max_concurrent": config.get("agents", {}).get("max_concurrent", 3)},
    }
    return safe
