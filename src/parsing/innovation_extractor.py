import json, logging
from pathlib import Path
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM = (
    '你是论文证据抽取助手。'
    '请从论文内容中提取"实现层面的创新点"及其可核查证据。'
    '不要写泛泛的"提升精度"。只输出合法 JSON。'
)

EXTRACTION_USER = """论文元数据：{metadata}
论文内容：{content}

请提取以下字段并以 JSON 输出：
- is_relevant: 是否能从输入中识别论文的研究任务和方法 (bool)；这里未提供会话方向，不得按预设学科排除论文，方向相关性由后续证据审计判断
- research_domain: 论文所属研究领域
- task_or_problem: 论文要解决的任务或问题
- method_summary: 一句话说明方法如何工作
- method_component: 字符串。论文方法的组成部分或处理环节，多个部分用分号连接；不适用时返回空字符串
- method_category: 方法大类。如 attention_mechanism, cnn, transformer, gnn, diffusion, nerf, iterative_optimization, traditional, hybrid
- method_subcategory: 具体方法子类名，如 cross_scale_attention
- innovation_detail: 精确描述怎么做的，包含计算方式、数据流、模块修改细节
- baseline_method: 基于哪个已有方法改进
- replaces_component: 替代了基准方法的哪个组件
- key_techniques: 3-5 个关键技术名词列表
- datasets: 使用的数据集列表
- performance: 关键性能指标 dict
- ablation_insights: 字符串。消融实验关键发现；未报告时返回空字符串
- limitations: 原文明确提到的限制、失败条件或未覆盖范围列表
- evidence: 证据列表，每条包含 section, quote, supports 字段。最多 10 条；如果论文涉及知识蒸馏，必须优先分别保留能直接证明教师信号、学生网络、模仿目标、蒸馏/匹配损失和迁移方向的原文句子。使用预训练模型、冻结编码器、特征融合或 attention 算子本身不等于蒸馏。supports 只能概括 quote 直接支持的事实，不能补写 quote 中没有的教师、目标或损失。
- confidence: 0-1 的置信度（有全文 method section 证据 0.8+，仅有摘要 0.4-0.65）

只输出 JSON。"""


class InnovationExtractor:
    def __init__(self, llm_client):
        self.llm = llm_client

    async def extract_from_markdown(self, paper_id: str, metadata: dict,
                                    markdown_path: Path) -> dict:
        content = markdown_path.read_text(encoding="utf-8")
        from src.parsing.content_quality import unusable_fulltext_reason
        reason=unusable_fulltext_reason(content)
        if reason:
            return {'paper_id':paper_id,'is_relevant':False,'confidence':0.0,'error':reason}
        if len(content) > 60000:
            content = content[:60000] + "\n...[truncated]"
        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": EXTRACTION_USER.format(
                metadata=json.dumps(metadata, ensure_ascii=False),
                content=content,
            )},
        ]
        try:
            result = await self.llm.chat_json(messages)
            profile = self._validate(result, paper_id)
            profile['extraction_model'] = getattr(self.llm, 'fast_model', None)
            return profile
        except Exception as e:
            logger.error(f"Innovation extraction failed for {paper_id}: {e}")
            return {
                "paper_id": paper_id, "is_relevant": False,
                "confidence": 0.0, "error": str(e),
            }

    async def extract_from_abstract(self, paper_id: str,
                                    metadata: dict) -> dict:
        if not str(metadata.get('abstract') or '').strip():
            return {'paper_id':paper_id,'is_relevant':False,'confidence':0.0,
                    'error':'仅有题录，没有摘要或全文，不能提取方法证据'}
        content = f"[只有摘要可用]\n\n{metadata.get('abstract', '')}"
        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": EXTRACTION_USER.format(
                metadata=json.dumps(metadata, ensure_ascii=False),
                content=content,
            )},
        ]
        try:
            result = await self.llm.chat_json(messages)
            profile = self._validate(result, paper_id)
            profile['extraction_model'] = getattr(self.llm, 'fast_model', None)
            return profile
        except Exception as e:
            logger.error(
                f"Innovation extraction (abstract) failed for {paper_id}: {e}"
            )
            return {
                "paper_id": paper_id, "is_relevant": False,
                "confidence": 0.0, "error": str(e),
            }

    @staticmethod
    def _validate(result, paper_id):
        from src.parsing.schemas import InnovationProfileSchema
        profile = InnovationProfileSchema.model_validate(result).model_dump()
        profile['paper_id'] = paper_id
        profile['has_fulltext'] = False  # Assigned by the parser, never by the model.
        if any(not isinstance(e.get('quote', ''), str) for e in profile['evidence']):
            raise ValueError('Evidence quote must be text')
        return profile
