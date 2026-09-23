import logging, json

logger = logging.getLogger(__name__)


class Critic:
    def __init__(self, llm_client):
        self.llm = llm_client

    async def critique(self, analysis: dict, innovations: list[dict]) -> dict:
        try:
            review_input = {
                "gaps": analysis.get("gaps", []),
                "matrix": analysis.get("matrix", {}),
                "claim_verdicts": analysis.get("claim_verdicts", []),
                "coverage_audit": analysis.get("coverage_audit", []),
                "coverage_records": analysis.get("coverage_records", []),
            }
            return await self.llm.chat_json([
                {"role": "system",
                 "content": (
                     "你是严格的研究空白论文审稿人。逐项裁决候选空白，只输出JSON。"
                     "不能只给全局免责声明；每个输入 gap 都必须返回一个 gap_reviews 项。"
                     "verdict 只能是 accept、revise、provisional、reject。"
                    "若已有论文直接覆盖宽泛主张、类别被混淆、最近工作无证据、"
                    "或创新仅是通用技巧组合，必须 provisional 或 reject。"
                    "研究假设尚未训练不等于缺乏文献依据，不得以尚无实验结果为唯一理由否决。"
                    "没有检索到不证明不存在。只能在本轮覆盖范围内评价候选；不要伪造用户主张。"
                     "最近工作必须来自 coverage_records 中与当前研究问题同任务、同实现目标的证据，"
                     "不得把无关方法当成最近工作；仅在研究方向涉及蒸馏时检查同教师、同目标和迁移方向。"
                     "revise 必须给 revised_description，并把主张收窄到证据能支持的范围。"
                     "confidence_cap 必须在0到1之间。warnings、suggestions 和 reason 使用中文。"
                 )},
                {"role": "user",
                 "content": (
                     f"待裁决分析与逐篇证据审计：{json.dumps(review_input, ensure_ascii=False)}\n\n"
                     "输出结构：{\"gap_reviews\":[{\"gap_id\":\"...\",\"gap_name\":\"...\","
                     "\"verdict\":\"accept|revise|provisional|reject\",\"reason\":\"...\","
                     "\"confidence_cap\":0.5,\"contradicted_by\":[\"paper_id\"],"
                     "\"revised_name\":\"可选\",\"revised_description\":\"revise时必填\"}],"
                     "\"warnings\":[\"...\"],\"suggestions\":[\"...\"]}。只输出JSON。"
                 )},
            ], model=self.llm.reasoning_model, purpose='analysis')
        except Exception:
            return {"warnings": [], "suggestions": []}
