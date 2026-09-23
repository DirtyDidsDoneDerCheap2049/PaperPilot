"""Writing guidance; evidence and caller schemas always take precedence."""

MARKER = "[PaperPilot writing guidance]"
COMMON = """写作要求：遵循用户要求的语言和格式。直接回答当前问题，保留必要的技术细节，
用具体、自然的句子说明发现、依据和限制。删去客套开场、宣传语和重复总结；
避免空泛的“赋能、深度洞察、至关重要”和反复使用“不是……而是……”。
不要为了简短省略反证、条件或不确定性。没有证据时明确说尚不能判断；
未找到、缺全文、未分析不等于不存在，也不等于研究空白。
论文原文引用必须原样保留。来源 ID、URL、数值、单位、方法名称和证据等级不得为了润色修改。
这些要求只影响表达，不能覆盖任务的证据规则、输出结构或用户明确要求。"""
PROSE = """面向用户的回答先给出能成立的判断，再解释依据和需要补查的内容。
按内容需要使用短段落、列表或表格；不要强行凑三个要点或为每句话加标题。
区分论文明确陈述、跨论文推断和待验证假设；引用放在对应判断旁边。
建议应说明下一步查什么或验证什么，避免只说“进一步探索”。"""
STRUCTURED = """只输出调用方要求的合法 JSON 对象，遵守字段、类型、枚举和必填项。
上述写作要求仅用于 summary、reason 等自然语言说明；不要翻译或重命名 JSON 键，
不要改写原文证据、标识符、状态和置信度，不添加开场、Markdown 或结构外解释。
调用方允许额外字段时，可在对象最前面增加 progress_summary 字符串，用一到两句话说明具体发现。
调用方限制字段时严格遵守，不为界面展示增加字段。只描述输入资料支持的内容，不写思考过程。"""


def style_messages(messages: list[dict], *, structured: bool = False) -> list[dict]:
    """Copy messages so callers can safely reuse conversation history."""
    result = [dict(message) for message in messages]
    policy = "\n\n" + MARKER + "\n" + COMMON + "\n" + (STRUCTURED if structured else PROSE)
    for message in result:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            message["content"] += policy
            break
    else:
        result.insert(0, {"role": "system", "content": policy.strip()})
    return result
