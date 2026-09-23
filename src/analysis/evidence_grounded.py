import json
import logging
import re


logger = logging.getLogger(__name__)


class EvidenceGroundedAnalyzer:
    """Audit every innovation profile before asking the LLM to propose gaps.

    The old pipeline concatenated every profile and cut the resulting JSON at a
    fixed character boundary.  That could silently remove the strongest
    counter-evidence.  This analyzer keeps paper boundaries intact, audits the
    profiles in bounded batches, and only sends validated local evidence IDs to
    the synthesis pass.
    """

    def __init__(self, llm_client, batch_size: int = 8):
        self.llm = llm_client
        self.batch_size = max(1, int(batch_size or 8))


    @staticmethod
    def _clip(value, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    @staticmethod
    def _as_list(value) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    @classmethod
    def compact_profile(cls, profile: dict) -> dict:
        """Keep a bounded, evidence-bearing representation of one paper."""
        paper_id = str(profile.get("paper_id") or "")
        evidence = []
        raw_evidence = list(enumerate(cls._as_list(profile.get("evidence"))))
        # Historical profiles append locally verified quotes after model excerpts.
        # Keep their original IDs, but never let unverified excerpts consume the cap.
        raw_evidence.sort(key=lambda pair: not (
            isinstance(pair[1], dict) and pair[1].get('source_verified')
            and profile.get('evidence_verified')
            and (pair[1].get('source_span') or {}).get('sha256') == profile.get('document_sha256')
        ))

        for index, item in raw_evidence:
            if len(evidence) >= 10:
                break
            if not isinstance(item, dict):
                item = {"quote": str(item)}
            quote = cls._clip(item.get("quote"), 1200 if item.get('source_verified') else 420)
            supports = cls._clip(item.get("supports"), 220)
            if not quote and not supports:
                continue
            evidence.append({
                "evidence_id": f"{paper_id}#e{index + 1}",
                "section": cls._clip(item.get("section") or item.get("page"), 120),
                "quote": quote,
                "supports": supports,
                "source_span": item.get('source_span') if item.get('source_verified') and profile.get('evidence_verified') and (item.get('source_span') or {}).get('sha256') == profile.get('document_sha256') else None,
                "evidence_level": (
                    "direct_fulltext" if profile.get("has_fulltext") and quote and item.get('source_verified') and profile.get('evidence_verified') and item.get('source_span',{}).get('sha256') == profile.get('document_sha256')
                    else "abstract_or_metadata" if quote
                    else "profile_inference"
                ),
            })
        return {
            "paper_id": paper_id,
            "paper_title": cls._clip(profile.get("paper_title") or profile.get("title"), 240),
            "venue": cls._clip(profile.get("venue"), 120),
            "source_type": cls._source_type(profile),
            "has_fulltext": bool(profile.get("has_fulltext")),
            "research_domain": cls._clip(profile.get("research_domain"), 140),
            "task_or_problem": cls._clip(profile.get("task_or_problem"), 240),
            "method_summary": cls._clip(profile.get("method_summary"), 500),
            "method_component": cls._clip(profile.get("method_component"), 160),
            "limitations": [cls._clip(v, 220) for v in cls._as_list(profile.get("limitations"))[:6]],
            "method_category": cls._clip(profile.get("method_category"), 100),
            "method_subcategory": cls._clip(profile.get("method_subcategory"), 140),
            "innovation_detail": cls._clip(
                profile.get("innovation_detail") or profile.get("contribution"), 900
            ),
            "key_techniques": [
                cls._clip(item, 100)
                for item in cls._as_list(profile.get("key_techniques"))[:8]
            ],
            "ablation_insights": cls._clip(profile.get("ablation_insights"), 420),
            "profile_confidence": profile.get("confidence"),
            "evidence": evidence,
        }


    @classmethod
    def _source_type(cls, profile: dict) -> str:
        explicit = str(profile.get("source_type") or "").strip().lower()
        allowed = {
            "paper_fulltext", "paper_abstract", "survey_or_repository",
            "prior_knowledge", "model_generated_summary",
        }
        if explicit in allowed:
            return explicit
        paper_id = str(profile.get("paper_id") or "").lower()
        title = str(profile.get("paper_title") or profile.get("title") or "").lower()
        venue = str(profile.get("venue") or "").lower()
        if paper_id.startswith("prior:") or venue == "prior_knowledge":
            return "prior_knowledge"
        survey_terms = (
            "awesome ", "reference repository", "taxonomy", "survey",
            "review of",
        )
        if any(term in title for term in survey_terms):
            return "survey_or_repository"
        has_quote = any(
            isinstance(item, dict) and str(item.get("quote") or "").strip()
            for item in cls._as_list(profile.get("evidence"))
        )
        if profile.get("has_fulltext") and has_quote:
            return "paper_fulltext"
        if not profile.get("has_fulltext") and has_quote:
            return "paper_abstract"
        return "model_generated_summary"

    @staticmethod
    def _text_blob(*values) -> str:
        parts = []
        for value in values:
            if isinstance(value, dict):
                parts.append(json.dumps(value, ensure_ascii=False))
            elif isinstance(value, list):
                parts.extend(str(item) for item in value)
            elif value is not None:
                parts.append(str(value))
        return " ".join(parts).lower()

    @staticmethod
    def _has(text: str, *terms: str) -> bool:
        return any(term.lower() in text for term in terms)

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


    @staticmethod
    def claims_from_direction(direction: dict) -> list[dict]:
        values = EvidenceGroundedAnalyzer._as_list(
            direction.get("claims_to_verify")
        )
        claims = []
        for index, value in enumerate(values):
            text = " ".join(str(value or "").split())
            if text:
                claims.append({"claim_id": f"claim_{index + 1}", "claim": text})
        if not claims:
            focus = " / ".join(
                str(direction.get(key) or "").strip()
                for key in ("research_question", "research_domain", "target_task", "method_category",
                            "method_subcategory", "method_component")
                if str(direction.get(key) or "").strip()
            )
            if focus:
                claims.append({
                    "claim_id": "claim_1",
                    "claim": f"核查该研究焦点是否已有直接或功能等价覆盖：{focus}",
                })
        return claims

    async def _chat_json(self, messages: list[dict]) -> dict:
        model = getattr(self.llm, "reasoning_model", None)
        if model:
            return await self.llm.chat_json(messages, model=model, purpose='analysis')
        return await self.llm.chat_json(messages)

    @classmethod
    def _method_facts(cls, item: dict, profile: dict) -> dict:
        """Bind method fields to actual quotes; semantic entailment still needs review."""
        evidence = {e['evidence_id']: e for e in profile.get('evidence', [])
                    if e.get('evidence_level') == 'direct_fulltext'}
        raw = item.get('method_facts')
        if not isinstance(raw, dict) or profile.get('source_type') != 'paper_fulltext':
            return {}
        facts = {}
        # General mechanism checks, independent of model family and application domain.
        kd_terms = {
            'teacher_signal': r'teacher|教师',
            'student': r'student|学生',
            'imitation_target': r'feature|logit|distribution|representation|attention|prediction|output|特征|分布|表示|注意力|预测|输出',
            'matching_loss': r'loss|objective|divergence|\bmse\b|\bkl\b|损失|优化目标|散度',
        }
        for key in ('task', 'mechanism', 'source_task', 'target_task', 'teacher_signal',
                    'student', 'imitation_target', 'matching_loss'):
            fact = raw.get(key)
            if not isinstance(fact, dict) or not str(fact.get('value') or '').strip():
                continue
            ids = [str(i) for i in cls._as_list(fact.get('evidence_ids')) if str(i) in evidence]
            if key in kd_terms:
                valid = []
                for eid in ids:
                    # Do not use model-written `supports` text to satisfy method requirements.
                    quote = evidence[eid].get('quote') or ''
                    sentences = re.split(r'[.!?。！？;；\n]', quote)
                    if any(re.search(kd_terms[key], s, re.I) and not re.search(
                        r'\b(?:no|not|without|never)\b|不使用|未使用|无需|没有|并非|不涉及|不是', s, re.I
                    ) for s in sentences):
                        valid.append(eid)
                ids = valid
            if ids:
                facts[key] = {'value': cls._clip(fact['value'], 260), 'evidence_ids': cls._unique(ids)}
        return facts

    def _normalize_batch(self, raw: dict, batch: list[dict]) -> list[dict]:
        raw_items = self._as_list(raw.get("paper_assessments")) if isinstance(raw, dict) else []
        by_id = {
            str(item.get("paper_id")): item
            for item in raw_items
            if isinstance(item, dict) and item.get("paper_id")
        }
        normalized = []
        for profile in batch:
            paper_id = profile["paper_id"]
            item = by_id.get(paper_id)
            if not item:
                normalized.append({
                    **profile,
                    "assessment_status": "unassessed",
                    "method_relations": [],
                    "claim_assessments": [],
                    "audit_warning": "本批审计没有返回该论文，不能据此判断未覆盖。",
                })
                continue

            allowed_evidence = {
                ev["evidence_id"] for ev in profile.get("evidence", [])
            }
            claim_assessments = []
            for assessment in self._as_list(item.get("claim_assessments")):
                if not isinstance(assessment, dict):
                    continue
                evidence_ids = [
                    str(eid) for eid in self._as_list(assessment.get("evidence_ids"))
                    if str(eid) in allowed_evidence
                ]
                has_direct_evidence = any(
                    ev.get("evidence_id") in evidence_ids
                    and ev.get("evidence_level") == "direct_fulltext"
                    for ev in profile.get("evidence", [])
                )
                verdict = str(assessment.get("verdict") or "insufficient_evidence")
                if verdict not in {'covered', 'partially_covered', 'contradicted', 'not_relevant', 'insufficient_evidence'}:
                    verdict = 'insufficient_evidence'
                if verdict in {"covered", "partially_covered", "contradicted"} and not has_direct_evidence:
                    verdict = "insufficient_evidence"
                claim_assessments.append({
                    "claim_id": str(assessment.get("claim_id") or ""),
                    "verdict": verdict,
                    "reason": self._clip(assessment.get("reason"), 420),
                    "evidence_ids": evidence_ids,
                    "evidence_level": (
                        "direct_fulltext" if has_direct_evidence
                        else "abstract_or_inference"
                    ),
                })
            relations = []
            for relation in self._as_list(item.get("method_relations")):
                relation = str(relation).strip().lower()
                if relation and len(relation) <= 80 and '|' not in relation and relation not in relations:
                    relations.append(relation)
            facts = self._method_facts(item, profile)
            unproven_distillation = 'distillation' in relations and not all(
                key in facts for key in ('teacher_signal', 'student', 'imitation_target', 'matching_loss')
            )
            if unproven_distillation:
                relations.remove('distillation')
                for assessment in claim_assessments:
                    if assessment['verdict'] in {'covered', 'partially_covered', 'contradicted'}:
                        assessment['verdict'] = 'insufficient_evidence'
                        assessment['reason'] = '蒸馏机制的原文依据不完整，需重新核查该方法及主张。'
            normalized_item = {
                **profile,
                "assessment_status": "audited",
                "method_relations": relations or ['other'],
                "method_facts": facts,
                "claim_assessments": claim_assessments,
            }
            normalized.append(normalized_item)
        return normalized


    async def _audit_batch(self, direction: dict, claims: list[dict],
                           batch: list[dict]) -> list[dict]:
        prompt = {
            "direction": direction,
            "claims_to_verify": claims,
            "profiles": batch,
        }
        try:
            result = await self._chat_json([
                {
                    "role": "system",
                    "content": (
                        "你是研究证据审计员。逐篇核对当前研究问题和主张，只输出JSON。"
                        "研究领域、目标任务和方法以输入为准，不要强行改写为其他领域。"
                        "用户的新颖性断言都是待核查假设。每篇论文返回一个 paper_assessments 项。"
                        "逐项说明已有实现是否覆盖主张，以及任务、条件和实现上的差异。"
                        "claim_assessments.verdict 只能是 covered、partially_covered、contradicted、"
                        "not_relevant、insufficient_evidence。只有 direct_fulltext 原文支持时才可确认覆盖或反证；"
                        "缺全文、未提及或检索未找到不证明空白。evidence_ids 必须来自该论文输入，不能编造。"
                        "method_relations 使用当前论文的方法名，不限于固定分类；无法判断时用 other。"
                        "method_facts 按需提供 task、mechanism、source_task、target_task，每项为 value 与 evidence_ids。"
                        "涉及迁移时分别给出来源与目标任务；反向迁移或不同任务不能视为同一实现的直接覆盖。"
                        "若声称 distillation，必须额外提供 teacher_signal、student、imitation_target、matching_loss 四项原文依据。"
                        "冻结编码器、特征融合、attention 算子或参数初始化本身不等于蒸馏；无依据则不能确认该机制。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        json.dumps(prompt, ensure_ascii=False)
                        + '\n输出结构：{"paper_assessments":[{"paper_id":"...",'
                        '"method_relations":["other"],"method_facts":{"mechanism":{"value":"原文方法",'
                        '"evidence_ids":["paper#e1"]}},"claim_assessments":[{"claim_id":"claim_1",'
                        '"verdict":"insufficient_evidence","reason":"中文","evidence_ids":["paper#e1"]}]}]}'
                    ),
                },
            ])
        except Exception as exc:
            logger.warning("Evidence audit batch failed: %s", exc)
            result = {
                "paper_assessments": [
                    self._deterministic_assessment(profile, claims)
                    for profile in batch
                ]
            }
        if not isinstance(result, dict):
            result = {}
        normalized = self._normalize_batch(result, batch)
        for item in normalized:
            raw_item = next((
                raw for raw in self._as_list(result.get("paper_assessments"))
                if isinstance(raw, dict)
                and str(raw.get("paper_id") or "") == str(item.get("paper_id") or "")
            ), {})
            if raw_item.get("audit_mode") == "deterministic_fallback":
                item["audit_mode"] = "deterministic_fallback"
                item["audit_warning"] = (
                    "语义审计不可用；仅保留原文证据，不确认覆盖、反证或新颖性主张。"
                )
        return normalized

    @classmethod
    def _deterministic_assessment(cls, profile: dict,
                                  claims: list[dict]) -> dict:
        return {
            "paper_id": profile.get("paper_id"),
            "audit_mode": "deterministic_fallback",
            "method_relations": ["other"],
            "claim_assessments": [
                {
                    "claim_id": claim.get("claim_id"),
                    "verdict": "insufficient_evidence",
                    "reason": "语义审计不可用，保留原文待核查，不用关键词分类替代裁决。",
                    "evidence_ids": [],
                }
                for claim in claims
            ],
        }

    async def analyze(self, direction: dict, innovations: list[dict]) -> dict:
        profiles = [
            self.compact_profile(item) for item in innovations
            if not item.get("error") and item.get("paper_id")
            and self._source_type(item) in {'paper_fulltext', 'paper_abstract'}
        ]
        if not profiles:
            return {"matrix": {}, "gaps": [], "coverage_audit": []}

        claims = self.claims_from_direction(direction)
        coverage_audit = []
        for start in range(0, len(profiles), self.batch_size):
            batch = profiles[start:start + self.batch_size]
            audited = await self._audit_batch(direction, claims, batch)
            missing = {p['paper_id'] for p in audited if p.get('assessment_status')!='audited'
                       or p.get('audit_mode')=='deterministic_fallback'}
            if missing:
                retried = await self._audit_batch(direction, claims, [p for p in batch if p['paper_id'] in missing])
                replacements = {p['paper_id']:p for p in retried}
                audited = [replacements.get(p['paper_id'],p) for p in audited]
            coverage_audit.extend(audited)

        synthesis_payload = {
            "direction": direction,
            "claims_to_verify": claims,
            "coverage_audit": coverage_audit,
        }
        try:
            result = await self._chat_json([
                {
                "role": "system",
                "content": (
                    "你是谨慎的研究空白顾问。基于逐篇证据审计构建覆盖矩阵，"
                    "先反证再提出窄义候选，只输出JSON。不得把用户主张当事实。"
                    "矩阵每个 covered/partial 单元必须给出本轮真实 paper_ids 和逐篇 inclusion_reasons；"
                    "全空矩阵只表示自动分类失败或本轮未找到，不证明文献不存在。"
                    "必须区分蒸馏、融合、自训练、压缩和迭代细化，不能用类别混淆制造空白。"
                    "仅仅组合成熟模块、换名或替换现成组件不构成强研究问题；"
                    "候选必须回答当前研究方向，说明可证伪的任务特定贡献；不得强行改成教师冲突或蒸馏问题。"
                    "每个候选必须有 gap_id、candidate_status、nearest_works、novelty_basis、"
                    "task_specific_contribution、evidence、required_evidence 和 decisive_experiment。"
                    "每个候选还必须有 name 和 description，直接说明研究机制和要解决的问题。"
                    'nearest_works 必须是对象数组：[{"paper_id":"输入中的ID","difference":"与该论文的具体差异"}]，不可用标题字符串或 baseline 字段代替。'
                    "区分文献核查与训练实验：实验尚未开展不等于没有可成立的研究假设；"
                    "不要要求先证明性能提升才允许提出有文献依据的候选。"
                    "用户询问研究空白不等于断言不存在已有工作，不得替用户编造强断言再反驳。"
                    "decisive_experiment 必须分别给出 nearest_method_baselines、minimal_change、"
                    "supporting_metrics、stop_conditions，不能复用通用三组实验模板。"
                    "candidate_status 只能是 supported_candidate、narrow_candidate、"
                    "insufficient_evidence、contradicted。contradicted 不得放入 gaps。"
                    "没有全文直接证据、没有本轮最近工作或只是‘未找到同名方法’的想法，"
                    "必须标为 insufficient_evidence 并放入 provisional_gaps。"
                    "evidence 必须包含 paper_id 和输入中对应的 evidence_ids。"
                ),
                },
                {
                "role": "user",
                "content": (
                    json.dumps(synthesis_payload, ensure_ascii=False)
                    + "\n输出字段：coverage_summary, claim_verdicts, matrix, gaps, "
                    "provisional_gaps, rejected_gaps, negative_evidence, alternative_angles。"
                    "matrix 至少两个实现维度；cells 项包含 coordinates, paper_ids, coverage, inclusion_reasons。"
                    "所有解释使用中文；paper_id、论文标题、模型名和数据集名可保留原文。"
                ),
                },
            ])
        except Exception as exc:
            logger.warning("Evidence synthesis failed: %s", exc)
            result = {
                "matrix": {},
                "gaps": [],
                "provisional_gaps": [],
                "coverage_summary": "逐篇证据审计已完成，但综合矩阵生成失败，不能推断研究空白。",
            }
        if not isinstance(result, dict):
            result = {}
        from src.analysis.direction_coverage import build_direction_coverage, ground_direction_candidates
        result["model_proposed_matrix"] = result.get("matrix", {})
        result['analysis_scope'] = 'research_direction'
        result['matrix'], result['coverage_records'] = build_direction_coverage(direction, claims, coverage_audit)
        result = ground_direction_candidates(result, result['coverage_records'])
        result["coverage_audit"] = coverage_audit
        result["claims_to_verify"] = claims
        return result
