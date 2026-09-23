import logging

logger = logging.getLogger(__name__)


class DirectionParser:
    def __init__(self, llm_client):
        self.llm = llm_client

    async def parse(self, user_message: str) -> dict:
        return await self.llm.chat_json([
            {"role": "system",
             "content": (
                 "你是研究空白核查助手。把用户提出的具体实现想法拆解为可检索、可反证的结构化字段。"
                 "不要把问题改写成泛泛的论文综述；只输出JSON。"
             )},
            {"role": "user",
             "content": (
                 f"用户输入：{user_message}\n\n"
                 "提取：research_question, research_domain, target_task, method_category, "
                 "method_subcategory, implementation_details, claims_to_verify, "
                 "search_queries(5-10条英文), matrix_axes_hint。"
                 "method_component 使用论文方向中的模型组件或处理环节，不限定枚举。迁移问题区分来源与目标任务。只输出JSON。"
             )},
        ])
