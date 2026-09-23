PERMISSION_RULES = {
    "read_workspace": "allow",
    "write_generated": "allow",
    "write_manual_notes": "ask",
    "delete_file": "deny",
    "external_api": "allow_log",
    "stdio_mcp": "ask_or_config_allow",
    "paywall_scrape": "deny",
}

def check_permission(action: str) -> str:
    return PERMISSION_RULES.get(action, "deny")

def is_allowed(action: str) -> bool:
    rule = check_permission(action)
    return rule in ("allow", "allow_log")
