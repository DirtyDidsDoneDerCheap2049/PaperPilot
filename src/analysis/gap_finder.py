import logging, json

logger = logging.getLogger(__name__)


class GapFinder:
    def __init__(self, llm_client):
        self.llm = llm_client

    async def find_gaps(self, direction: dict, matrix: dict,
                        innovations: list[dict]) -> dict:
        from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
        profiles_text = json.dumps([
            EvidenceGroundedAnalyzer.compact_profile(profile)
            for profile in innovations
            if profile.get("is_relevant", True) and profile.get("paper_id")
        ], ensure_ascii=False)
        return await self.llm.chat_json([
            {"role": "system",
             "content": (
                 "你是谨慎的研究空白顾问。分析当前方向的创新空白。不要编造论文。"
                 "方向中的新颖性断言是待核查假设。不得把融合、自训练或模型压缩误写为蒸馏。"
                 "全空矩阵只表示分类失败，不能作为不存在相关工作的证据。"
                 "每个空白必须给出：name, description, feasibility(1-5), novelty(1-5),"
                 " difficulty(1-5), confidence(0-1), candidate_status, novelty_basis,"
                 " task_specific_contribution, nearest_works列表(含paper_id和difference)。"
                 "没有真实最近工作和直接差异的想法必须标为 insufficient_evidence。"
                 "只输出JSON。"
             )},
            {"role": "user",
             "content": (
                 f"方向：{json.dumps(direction)}\n"
                 f"矩阵：{json.dumps(matrix)}\n"
                 f"画像：{profiles_text}\n\n"
                 "找空白。只输出JSON。"
             )},
        ], model=self.llm.reasoning_model, purpose='analysis')
