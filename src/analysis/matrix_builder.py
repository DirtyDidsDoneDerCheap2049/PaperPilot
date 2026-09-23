import logging

logger = logging.getLogger(__name__)


class MatrixBuilder:
    def __init__(self, llm_client):
        self.llm = llm_client

    async def build(self, direction: dict, innovations: list[dict]) -> dict:
        relevant = [i for i in innovations if i.get("is_relevant", True)]
        if not relevant:
            return {"axes": [], "cells": [], "message": "No relevant papers"}
        import json
        from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
        profiles_text = json.dumps([
            EvidenceGroundedAnalyzer.compact_profile(profile)
            for profile in relevant if profile.get("paper_id")
        ], ensure_ascii=False)
        return await self.llm.chat_json([
            {"role": "system",
             "content": (
                 "你是研究空白顾问。从创新画像中构建当前方向的覆盖矩阵。只输出JSON。"
                 "方向中的新颖性断言是待核查假设，不是事实。必须区分蒸馏、融合、自训练和压缩。"
                 "covered/partial 单元必须列出输入中真实 paper_id 和 inclusion_reasons；"
                 "empty 单元只表示本轮未归类，不能证明研究不存在。"
             )},
            {"role": "user",
             "content": f"方向：{json.dumps(direction, ensure_ascii=False)}\n画像：{profiles_text}\n\n构建创新覆盖矩阵。只输出JSON。"},
        ])
