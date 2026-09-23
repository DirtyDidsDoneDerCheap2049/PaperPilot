from pathlib import Path

WORKSPACE_TEMPLATE_DIRS = [
    "papers/arxiv", "papers/manual", "papers/parsed",
    "notes/generated", "notes/manual",
    "reports", "db/chroma", ".agent_history",
]

DEFAULT_CONFIG = """workspace:
  name: "{name}"
  research_field: "general_research"
server:
  host: "127.0.0.1"
  port: 8765
llm:
  provider: "deepseek"
  base_url: "https://api.deepseek.com"
  api_key_env: "DEEPSEEK_API_KEY"
  fast_model: "deepseek-flash"
  reasoning_model: "deepseek-flash"
  max_output_tokens: 65536
  regular_effort: "max"
  analysis_effort: "max"
  timeout_seconds: 600
mineru:
  enabled: false
  token_env: "MINERU_API_TOKEN"
  preferred_mode: "auto"
  model_version: "vlm"
  language: "en"
  enable_formula: true
  enable_table: true
  poll_interval_seconds: 3
  poll_timeout_seconds: 600
mcp:
  enabled: true
  connect_timeout_seconds: 20
  servers: []
search:
  enable_mcp: true
  enable_semantic_scholar: true
  enable_arxiv: true
  enable_openalex: false
  max_rounds: 3
  top_k_per_round: 20
  deep_parse_top_k: 50
research:
  max_model_calls: 200
  timeout_minutes: 120
embedding:
  provider: "local"
  model: "BAAI/bge-large-en-v1.5"
  local_model: "BAAI/bge-base-en-v1.5"
agents:
  max_concurrent: 3
  allow_background: true
  require_confirm_for_stdio_mcp: true
"""


def get_template_dirs() -> list[str]:
    return WORKSPACE_TEMPLATE_DIRS


def get_default_config(name: str = "research") -> str:
    return DEFAULT_CONFIG.format(name=name)
