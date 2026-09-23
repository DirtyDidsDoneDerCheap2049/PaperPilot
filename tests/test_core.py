import pytest, sys, os, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.workspace.manager import WorkspaceManager
from src.knowledge.sqlite_store import SQLiteStore
from src.tools.search_tools import PaperMetadata, merge_and_deduplicate



def synthetic_profile(profile):
    """These semantic fixtures use invented text, never real-paper accuracy labels."""
    from src.analysis.provenance import bind_profile
    source = "\n".join(str(ev.get("quote", "")) for ev in profile.get("evidence", []) if isinstance(ev, dict))
    return bind_profile(profile, source, "synthetic-test-fixture.txt")


class TestWorkspace:
    def test_create_workspace(self, tmp_path):
        ws = WorkspaceManager.create_workspace(tmp_path / "test_ws", "test")
        assert (tmp_path / "test_ws" / "config.yaml").exists()
        assert (tmp_path / "test_ws" / "papers" / "arxiv").is_dir()
        config = ws.load_config()
        assert config["workspace"]["name"] == "test"


class TestSQLite:
    def test_schema_init(self, tmp_path):
        db = SQLiteStore(tmp_path / "test.db")
        db.init_schema()
        tables = db.fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        names = [t["name"] for t in tables]
        assert "papers" in names
        assert "sessions" in names
        assert "messages" in names
        assert "agent_runs" in names
        assert "innovation_profiles" in names

    def test_paper_crud(self, tmp_path):
        db = SQLiteStore(tmp_path / "test.db")
        db.init_schema()
        db.execute(
            "INSERT INTO papers (id, title, abstract) VALUES (?,?,?)",
            ("test1", "Test Paper", "An abstract"),
        )
        row = db.fetchone("SELECT * FROM papers WHERE id=?", ("test1",))
        assert row["title"] == "Test Paper"


class TestSearch:
    def test_merge_dedup(self):
        p1 = PaperMetadata(id="a", title="Paper A", doi="10.1234/a")
        p2 = PaperMetadata(id="b", title="Paper A", doi="10.1234/a")
        p3 = PaperMetadata(id="c", title="Paper C", arxiv_id="2301.00001")
        import asyncio
        result = asyncio.run(merge_and_deduplicate([p1, p2, p3]))
        assert len(result) == 2

    def test_empty_merge(self):
        import asyncio
        result = asyncio.run(merge_and_deduplicate([]))
        assert len(result) == 0

    def test_merge_accepts_dict_metadata(self):
        import asyncio
        result = asyncio.run(merge_and_deduplicate([
            {"id": "local:p1", "title": "Local Stereo Paper", "doi": "10.1/a"},
            PaperMetadata(id="s2:p1", title="Local Stereo Paper", doi="10.1/a"),
        ]))
        assert len(result) == 1
        assert result[0].id == "local:p1"


class TestConfig:
    def test_config_load(self, tmp_path):
        ws = WorkspaceManager.create_workspace(tmp_path / "test_ws", "test")
        config = ws.load_config()
        assert "server" in config
        assert "llm" in config
        assert config["llm"]["fast_model"] == "deepseek-flash"
        assert config["embedding"]["provider"] == "local"

    def test_agent_models_do_not_share_mutable_defaults(self):
        from src.agents.base import AgentContext, AgentResult

        first_context = AgentContext()
        second_context = AgentContext()
        first_context.config["changed"] = True
        assert second_context.config == {}

        first_result = AgentResult(status="ok")
        second_result = AgentResult(status="ok")
        first_result.data["changed"] = True
        assert second_result.data == {}


class TestWorkspacePath:
    def test_workspace_id(self, tmp_path):
        ws = WorkspaceManager(tmp_path / "test_ws")
        ws.root.mkdir(parents=True, exist_ok=True)
        (ws.root / "config.yaml").touch()
        wid = ws.get_workspace_id()
        assert len(wid) > 0
        ws2 = WorkspaceManager(tmp_path / "test_ws")
        assert ws2.get_workspace_id() == wid


class TestAppFactory:
    def test_desktop_build_app_delegates_to_shared_factory(self, tmp_path, monkeypatch):
        from src.app import factory
        from src.app.desktop import _build_app

        expected = (object(), {"server": {}})
        captured = {}

        def fake_build_app(workspace_root, *, enable_file_logging=False):
            captured["workspace_root"] = workspace_root
            captured["file_logging"] = enable_file_logging
            return expected

        monkeypatch.setattr(factory, "build_app", fake_build_app)
        result = _build_app(tmp_path / "workspace")

        assert result == expected
        assert captured["workspace_root"] == tmp_path / "workspace"
        assert captured["file_logging"] is True

    def test_file_logging_handler_is_not_duplicated(self, tmp_path):
        import logging
        from src.app.factory import _ensure_file_logging

        log_path = (tmp_path / ".agent_history" / "app.log").resolve()
        root_logger = logging.getLogger()
        before = list(root_logger.handlers)
        try:
            _ensure_file_logging(tmp_path)
            _ensure_file_logging(tmp_path)
            matching = [
                handler for handler in root_logger.handlers
                if isinstance(handler, logging.FileHandler)
                and Path(handler.baseFilename).resolve() == log_path
            ]
            assert len(matching) == 1
        finally:
            for handler in list(root_logger.handlers):
                if handler not in before:
                    root_logger.removeHandler(handler)
                    handler.close()


class TestJsonSchemas:
    def test_direction_parse_result(self):
        from src.parsing.schemas import DirectionParseResult
        d = DirectionParseResult(
            stereo_matching_stage="cost_volume_construction",
            method_category="attention_mechanism",
            search_queries=["stereo matching"],
        )
        assert d.method_component == "cost_volume_construction"

    def test_innovation_profile_schema(self):
        from src.parsing.schemas import InnovationProfileSchema
        p = InnovationProfileSchema(
            paper_id="t1",
            innovation_detail="detail",
            confidence=0.85,
        )
        assert p.confidence == 0.85

    def test_gap_analysis_result(self):
        from src.parsing.schemas import GapAnalysisResult
        g = GapAnalysisResult(
            gaps=[{"name": "test", "feasibility": 4}],
            matrix={"axes": ["a"]},
        )
        assert len(g.gaps) == 1


class TestReportGenerator:
    def test_report_written(self, tmp_path):
        from src.analysis.report_generator import ReportGenerator
        gen = ReportGenerator()
        path = gen.generate(
            tmp_path, {"test": True}, [], [], {"gaps": [], "matrix": {}}, []
        )
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "推荐下一步" in content

    def test_orchestrator_report_is_readable_chinese(self, tmp_path):
        import asyncio
        from src.agents.orchestrator import Orchestrator
        from src.agents.base import AgentContext

        db = SQLiteStore(tmp_path / "test.db")
        db.init_schema()
        db.execute(
            "INSERT INTO papers (id, title, retrieval_status) VALUES (?,?,?)",
            ("p1", "Monocular Prior Stereo Paper", "parsed"),
        )
        orch = Orchestrator(db, None, None, None)
        ctx = AgentContext(workspace_id="ws", session_id="s", workspace_root=tmp_path, config={})
        path = asyncio.run(orch._generate_report(
            ctx,
            {"method_category": "Monocular Depth Prior"},
            [{"id": "p1", "title": "Monocular Prior Stereo Paper", "retrieval_status": "parsed", "source": "selected_library"}],
            [{"paper_id": "p1", "innovation_detail": "uses monocular prior", "has_fulltext": True}],
            {"gaps": [{"name": "Uncertainty-aware prior fusion", "description": "Use uncertainty to weight prior fusion", "feasibility": 4, "novelty": 4, "difficulty": 3, "confidence": 0.8}], "matrix": {}},
            [],
        ))
        content = path.read_text(encoding="utf-8")
        assert "结论先行" in content
        assert "相关论文地图" in content
        assert "分析底座统计" in content
        assert "通过证据门与 Critic 的窄义候选" in content
        assert "推荐下一步" in content
        assert "外部二次研判" in content
        assert "原文证据 ID" in content

    def test_normalize_analysis_accepts_chinese_gap_keys(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        analysis = orch._normalize_analysis({
            "候选创新空白": [
                {
                    "名称": "不确定性感知单目先验融合",
                    "核心想法": "用不确定性调节单目深度先验注入强度",
                    "可行性": 4,
                    "新颖性": 4,
                    "难度": 3,
                    "置信度": 0.75,
                    "最接近工作": [{"paper_id": "p1", "difference": "缺少不确定性门控"}],
                }
            ],
            "matrix": {},
        })

        assert len(analysis["gaps"]) == 1
        assert analysis["gaps"][0]["name"] == "不确定性感知单目先验融合"
        assert analysis["gaps"][0]["description"] == "用不确定性调节单目深度先验注入强度"

    def test_followup_direction_recovers_previous_session_context(self, tmp_path):
        import asyncio
        import json
        from src.agents.orchestrator import Orchestrator

        class EmptyDirectionLLM:
            async def chat_json(self, messages, model=None, *, purpose='regular'):
                return {}

        previous = {
            "stereo_matching_stage": "disparity refinement",
            "method_category": "monocular depth prior",
            "method_subcategory": "uncertainty-guided fusion",
            "implementation_details": ["gate VFM features"],
            "search_queries": ["uncertainty guided monocular depth prior stereo matching"],
            "matrix_axes_hint": ["injection point", "gating signal"],
        }
        db = SQLiteStore(tmp_path / "followup.db")
        db.init_schema()
        db.execute(
            "INSERT INTO sessions (id, workspace_id, title) VALUES (?,?,?)",
            ("s:followup", "ws", "prior analysis"),
        )
        db.execute(
            "INSERT INTO messages (id, session_id, role, content) VALUES (?,?,?,?)",
            ("m:prior", "s:followup", "user", "研究单目深度先验与双目匹配"),
        )
        db.execute(
            "INSERT INTO gap_analyses (id, session_id, direction) VALUES (?,?,?)",
            ("g:prior", "s:followup", json.dumps(previous)),
        )
        orch = Orchestrator(db, EmptyDirectionLLM(), None, None)

        parsed = asyncio.run(orch._parse_direction(
            "regenerate the report from the previous discussion",
            previous_direction=orch._latest_session_direction("s:followup"),
            recent_context=orch._recent_direction_context("s:followup"),
        ))

        assert parsed["method_component"] == "disparity refinement"
        assert parsed["method_category"] == "monocular depth prior"
        assert previous["search_queries"][0] in parsed["search_queries"]
        assert parsed["context_recovered"] is True
        assert orch._direction_is_usable(parsed) is True

    def test_reliability_gate_blocks_gaps_without_matrix(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        result = orch._enforce_analysis_reliability(
            {"method_category": "monocular depth prior"},
            [{"paper_id": "p1", "has_fulltext": True}],
            {
                "matrix": {},
                "gaps": [{
                    "name": "unsupported candidate",
                    "confidence": 0.9,
                    "nearest_works": [{"paper_id": "p1"}],
                }],
            },
        )

        assert result["gaps"] == []
        assert result["provisional_gaps"][0]["name"] == "unsupported candidate"
        assert result["reliability"]["status"] == "blocked"
        assert result["reliability"]["blocked_gap_count"] == 1

    def test_explicit_empty_gaps_never_promotes_provisional_candidates(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        result = orch._normalize_analysis({
            "gaps": [],
            "provisional_gaps": [{"gap_id": "draft", "name": "待验证草案"}],
            "matrix": {},
        })

        assert result["gaps"] == []
        assert [gap["gap_id"] for gap in result["provisional_gaps"]] == ["draft"]

    def test_reliability_gate_accepts_chinese_matrix_and_caps_confidence(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        result = orch._enforce_analysis_reliability(
            {"method_category": "monocular depth prior"},
            [{"paper_id": "p1", "has_fulltext": False}],
            {
                "创新矩阵": {
                    "axes": ["注入位置", "门控信号"],
                    "cells": [{"paper_ids": ["p1"], "coverage": "partial"}],
                },
                "gaps": [
                    {
                        "name": "evidence-linked",
                        "candidate_status": "supported_candidate",
                        "confidence": 0.9,
                        "nearest_works": [{"paper_id": "p1", "difference": "只覆盖输出层"}],
                        "novelty_basis": "在立体几何冲突区域采用不同监督",
                        "decisive_experiment": {
                            "nearest_method_baselines": ["direct baseline"],
                            "minimal_change": "只替换冲突区域监督",
                            "supporting_metrics": ["区域EPE"],
                            "stop_conditions": ["不超过直接基线则停止"],
                        },
                    },
                    {
                        "name": "missing-nearest-work",
                        "candidate_status": "supported_candidate",
                        "confidence": 0.9,
                        "nearest_works": [{"paper_id": "not-in-evidence-base", "difference": "未知"}],
                        "novelty_basis": "尚未核实",
                    },
                ],
            },
        )

        assert result["reliability"]["matrix_valid"] is True
        assert result["gaps"][0]["confidence"] == 0.55
        assert len(result["gaps"]) == 1
        assert result["provisional_gaps"][0]["name"] == "missing-nearest-work"
        assert result["reliability"]["status"] == "degraded"

    def test_report_explains_candidates_blocked_by_reliability_gate(self, tmp_path):
        import asyncio
        from src.agents.base import AgentContext
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        analysis = orch._enforce_analysis_reliability(
            {"method_category": "monocular depth prior"},
            [{"paper_id": "p1", "has_fulltext": True}],
            {
                "matrix": {},
                "gaps": [{"name": "draft", "confidence": 0.9}],
            },
        )
        ctx = AgentContext(workspace_root=tmp_path)
        path = asyncio.run(orch._generate_report(
            ctx,
            {"method_category": "monocular depth prior"},
            [{"id": "p1", "title": "Paper", "retrieval_status": "parsed"}],
            [{"paper_id": "p1", "has_fulltext": True}],
            analysis,
            [],
        ))
        content = path.read_text(encoding="utf-8")

        assert "待验证想法：1 个" in content
        assert "当前不能声称为研究空白" in content

    def test_reliability_gate_blocks_structural_but_all_empty_matrix(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        result = orch._enforce_analysis_reliability(
            {"method_category": "knowledge distillation"},
            [{"paper_id": "aio", "has_fulltext": True}],
            {
                "matrix": {
                    "axes": ["蒸馏目标", "教师类型"],
                    "cells": [
                        {"coordinates": {"蒸馏目标": "特征", "教师类型": "多VFM"},
                         "paper_ids": ["aio"], "coverage": "empty"},
                    ],
                },
                "provisional_gaps": [{
                    "gap_id": "g0", "name": "existing provisional",
                    "candidate_status": "insufficient_evidence",
                }],
                "gaps": [{
                    "gap_id": "g1",
                    "name": "中间特征蒸馏",
                    "candidate_status": "supported_candidate",
                    "novelty_basis": "声称尚未覆盖",
                    "nearest_works": [{"paper_id": "aio", "difference": "错误差异"}],
                }],
            },
        )

        assert result["gaps"] == []
        assert result["reliability"]["matrix_structural_valid"] is True
        assert result["reliability"]["matrix_valid"] is False
        assert "全空矩阵" in result["reliability"]["warnings"][0]

    def test_reliability_gate_rejects_contradicted_and_holds_generic_gap(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        result = orch._enforce_analysis_reliability(
            {"method_category": "knowledge distillation"},
            [{"paper_id": "aio", "has_fulltext": True}],
            {
                "matrix": {
                    "axes": ["蒸馏目标", "教师类型"],
                    "cells": [{"paper_ids": ["aio"], "coverage": "covered"}],
                },
                "gaps": [
                    {"gap_id": "g1", "name": "已覆盖", "candidate_status": "contradicted"},
                    {"gap_id": "g2", "name": "只有组合想法"},
                ],
            },
        )

        assert result["gaps"] == []
        assert result["rejected_gaps"][0]["name"] == "已覆盖"
        assert result["provisional_gaps"][0]["name"] == "只有组合想法"

    def test_critic_verdicts_change_formal_gap_set(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        analysis = {
            "matrix": {"axes": ["a", "b"], "cells": [{"paper_ids": ["p1"]}]},
            "gaps": [
                {"gap_id": "g1", "name": "covered", "confidence": 0.8},
                {"gap_id": "g2", "name": "needs work", "confidence": 0.7},
                {"gap_id": "g3", "name": "narrow", "confidence": 0.7},
            ],
            "reliability": {"status": "ok", "warnings": []},
        }
        result = orch._apply_critic_verdicts(
            analysis,
            {"gap_reviews": [
                {"gap_id": "g1", "verdict": "reject", "reason": "AIO已覆盖", "confidence_cap": 0.0},
                {"gap_id": "g2", "verdict": "provisional", "reason": "只有摘要证据", "confidence_cap": 0.4},
                {"gap_id": "g3", "verdict": "revise", "reason": "需收窄", "confidence_cap": 0.55,
                 "revised_description": "仅研究几何证据驱动的教师冲突识别"},
            ]},
            [{"paper_id": "p1", "has_fulltext": True}],
        )

        assert result["gaps"] == []
        assert result["provisional_gaps"][1]["confidence"] == 0.55
        assert result["provisional_gaps"][1]["revision_requires_verification"] is True
        assert result["provisional_gaps"][0]["gap_id"] == "g2"
        assert result["rejected_gaps"][0]["gap_id"] == "g1"

    def test_evidence_analyzer_audits_every_profile_without_tail_truncation(self):
        import asyncio
        import json
        from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer

        class FakeLLM:
            reasoning_model = "reasoning"

            def __init__(self):
                self.calls = []

            async def chat_json(self, messages, model=None, *, purpose='regular'):
                content = messages[-1]["content"]
                self.calls.append(content)
                if "输出结构：" in content:
                    payload = json.loads(content.split("\n输出结构：", 1)[0])
                    return {"paper_assessments": [
                        {
                            "paper_id": profile["paper_id"],
                            "method_relations": ["distillation"],
                            "claim_assessments": [{
                                "claim_id": "claim_1",
                                "verdict": "partially_covered",
                                "reason": "已有部分覆盖",
                                "evidence_ids": [profile["evidence"][0]["evidence_id"]],
                            }],
                        }
                        for profile in payload["profiles"]
                    ]}
                payload = json.loads(content.split("\n输出字段：", 1)[0])
                paper_ids = [item["paper_id"] for item in payload["coverage_audit"]]
                return {
                    "matrix": {
                        "axes": ["目标", "教师"],
                        "cells": [{"paper_ids": paper_ids, "coverage": "partial"}],
                    },
                    "gaps": [],
                }

        innovations = [
            synthetic_profile({
                "paper_id": f"paper-{index:02d}",
                "is_relevant": True,
                "has_fulltext": True,
                "innovation_detail": f"innovation {index}",
                "evidence": [{"section": "Method", "quote": f"evidence {index}", "supports": "method"}],
            })
            for index in range(17)
        ]
        llm = FakeLLM()
        result = asyncio.run(EvidenceGroundedAnalyzer(llm, batch_size=5).analyze(
            {"claims_to_verify": ["没有中间特征蒸馏"]}, innovations
        ))

        assert len(llm.calls) == 5  # 4 audit batches + 1 synthesis
        assert [item["paper_id"] for item in result["coverage_audit"]] == [
            f"paper-{index:02d}" for index in range(17)
        ]
        assert all(item["assessment_status"] == "audited" for item in result["coverage_audit"])
        synthesis_call = llm.calls[-1]
        assert "paper-00" in synthesis_call
        assert "paper-16" in synthesis_call
        assert "...[truncated]" not in synthesis_call

    def test_evidence_audit_downgrades_strong_verdict_without_direct_quote(self):
        from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer

        analyzer = EvidenceGroundedAnalyzer(None)
        batch = [synthetic_profile({
            "paper_id": "d-fuse",
            "has_fulltext": False,
            "innovation_detail": "摘要级画像",
            "evidence": [{
                "evidence_id": "d-fuse#e1",
                "quote": "abstract only",
                "supports": "可能相关",
                "evidence_level": "abstract_or_metadata",
            }],
        })]
        result = analyzer._normalize_batch(
            {"paper_assessments": [{
                "paper_id": "d-fuse",
                "method_relations": ["fusion"],
                "claim_assessments": [{
                    "claim_id": "claim_1",
                    "verdict": "contradicted",
                    "reason": "声称已完成反证",
                    "evidence_ids": ["d-fuse#e1"],
                }],
            }]},
            batch,
        )

        assessment = result[0]["claim_assessments"][0]
        assert assessment["verdict"] == "insufficient_evidence"
        assert assessment["evidence_level"] == "abstract_or_inference"








    def test_reliability_gate_requires_structured_discriminative_experiment(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        result = orch._enforce_analysis_reliability(
            {"method_category": "knowledge distillation"},
            [{"paper_id": "p1", "has_fulltext": True}],
            {
                "matrix": {
                    "axes": ["teacher", "target"],
                    "cells": [{"paper_ids": ["p1"], "coverage": "partial"}],
                },
                "gaps": [{
                    "gap_id": "generic", "name": "generic experiment",
                    "candidate_status": "narrow_candidate", "confidence": 0.6,
                    "nearest_works": [{"paper_id": "p1", "difference": "adjacent"}],
                    "novelty_basis": "narrow difference",
                    "decisive_experiment": "compare baseline and proposed method",
                }],
            },
        )

        assert result["gaps"] == []
        assert result["provisional_gaps"][0]["gap_id"] == "generic"
        assert "停止条件" in result["provisional_gaps"][0]["reliability_notes"][0]



    def test_fallback_audit_blocks_model_candidate_even_with_valid_matrix(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        result = orch._enforce_analysis_reliability(
            {"method_category": "knowledge distillation"},
            [{"paper_id": "aio", "has_fulltext": True, "source_type": "paper_fulltext"}],
            {
                "coverage_records": [{
                    "paper_id": "aio", "coverage_eligible": True,
                    "coverage_level": "direct",
                }],
                "coverage_audit": [{
                    "paper_id": "aio", "assessment_status": "audited",
                    "audit_mode": "deterministic_fallback",
                }],
                "matrix": {
                    "axes": ["teacher", "target"],
                    "cells": [{"paper_ids": ["aio"], "coverage": "direct"}],
                },
                "provisional_gaps": [{
                    "gap_id": "g0", "name": "existing provisional",
                    "candidate_status": "insufficient_evidence",
                }],
                "gaps": [{
                    "gap_id": "g1", "name": "model candidate",
                    "candidate_status": "narrow_candidate",
                    "nearest_works": [{"paper_id": "aio", "difference": "narrow"}],
                    "novelty_basis": "claimed difference",
                    "decisive_experiment": {
                        "nearest_method_baselines": ["AIO"],
                        "minimal_change": "one gate",
                        "supporting_metrics": ["EPE"],
                        "stop_conditions": ["no gain"],
                    },
                }],
            },
        )

        assert result["gaps"] == []
        assert {gap["gap_id"] for gap in result["provisional_gaps"]} == {"g0", "g1"}
        assert result["reliability"]["status"] == "blocked"
        assert result["reliability"]["deterministic_fallback_profiles"] == 1
        assert result["reliability"]["blocked_gap_count"] == 2

    def test_gap_analysis_persists_request_selection_provenance(self, tmp_path):
        import json
        from pathlib import Path
        from src.agents.orchestrator import Orchestrator
        from src.knowledge.sqlite_store import SQLiteStore

        db = SQLiteStore(tmp_path / "analysis.db")
        db.init_schema()
        orch = Orchestrator(db, None, None, None)
        papers = [
            {"id": "selected", "title": "Selected", "source": "selected_library"},
            {"id": "counter", "title": "Counter", "source": "local_counterevidence"},
            {"id": "kb", "title": "KB", "source": "local_kb"},
        ]
        orch._save_gap_analysis(
            "session", {}, papers, [],
            {"gaps": [], "matrix": {}, "reliability": {}},
            [], Path("report.md"), selected_paper_ids=["selected"],
        )
        row = db.fetchone("SELECT search_log_json FROM gap_analyses LIMIT 1")
        saved = json.loads(row.get("search_log_json") or "{}")

        assert saved["selected_paper_ids"] == ["selected"]
        assert saved["selection_provenance"] == {
            "explicitly_selected_this_request": 1,
            "retrieved_from_selected_library": 1,
            "added_as_counterevidence": 1,
            "added_from_local_kb": 1,
            "total_unique_records": 3,
        }



class TestSessionDelete:
    def test_delete_session(self, tmp_path):
        from src.knowledge.sqlite_store import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.init_schema()
        db.execute("INSERT INTO sessions (id, workspace_id, title) VALUES (?,?,?)",
                   ("s1", "ws1", "Test"))
        db.execute("INSERT INTO messages (id, session_id, role, content) VALUES (?,?,?,?)",
                   ("m1", "s1", "user", "hello"))
        db.execute("INSERT INTO agent_runs (id, session_id, agent_name, status) VALUES (?,?,?,?)",
                   ("r1", "s1", "SearchAgent", "completed"))
        db.execute("INSERT INTO gap_analyses (id, session_id, direction) VALUES (?,?,?)",
                   ("g1", "s1", "test"))

        # Simulate DELETE
        db.execute("DELETE FROM tool_calls WHERE agent_run_id IN (SELECT id FROM agent_runs WHERE session_id='s1')")
        db.execute("DELETE FROM agent_runs WHERE session_id='s1'")
        db.execute("DELETE FROM messages WHERE session_id='s1'")
        db.execute("DELETE FROM gap_analyses WHERE session_id='s1'")
        db.execute("DELETE FROM sessions WHERE id='s1'")

        assert db.fetchone("SELECT id FROM sessions WHERE id='s1'") is None
        assert len(db.fetchall("SELECT id FROM messages WHERE session_id='s1'")) == 0
        assert len(db.fetchall("SELECT id FROM agent_runs WHERE session_id='s1'")) == 0

    def test_delete_nonexistent_session(self, tmp_path):
        from src.knowledge.sqlite_store import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.init_schema()
        # Should not crash
        db.execute("DELETE FROM sessions WHERE id='nonexistent'")




class TestWorkspaceSafety:
    def test_path_inside_workspace(self, tmp_path):
        from src.server.routes_workspace import _check_in_ws
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "test.md").write_text("hello")
        fp = _check_in_ws(ws, "test.md")
        assert fp.name == "test.md"

    def test_path_escape_blocked(self, tmp_path):
        from src.server.routes_workspace import _check_in_ws
        from fastapi import HTTPException
        ws = tmp_path / "ws"
        ws.mkdir()
        try:
            _check_in_ws(ws, "../../etc/passwd")
            assert False, "Should have raised"
        except HTTPException as e:
            assert e.status_code == 403

    def test_path_traversal_blocked(self, tmp_path):
        from src.server.routes_workspace import _check_in_ws
        from fastapi import HTTPException
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            _check_in_ws(ws, str(outside))
            assert False, "Should have raised"
        except HTTPException as e:
            assert e.status_code == 403

    def test_sibling_prefix_escape_blocked(self, tmp_path):
        from src.server.routes_workspace import _check_in_ws
        from fastapi import HTTPException
        ws = tmp_path / "ws"
        sibling = tmp_path / "ws_evil"
        ws.mkdir()
        sibling.mkdir()
        target = sibling / "secret.md"
        target.write_text("secret")
        try:
            _check_in_ws(ws, str(target))
            assert False, "Should have raised"
        except HTTPException as e:
            assert e.status_code == 403


class TestKnowledgeBase:
    def test_search_related_queries_vector_store(self, tmp_path):
        import asyncio
        from src.knowledge.kb_manager import KBManager

        class Hit:
            def __init__(self, id, distance=0.1):
                self.id = id
                self.distance = distance

        class FakeVectorStore:
            def __init__(self):
                self.ids = []

            def add_documents(self, collection, docs):
                if collection == "papers":
                    self.ids.extend(d.id for d in docs)

            def query(self, collection, query, top_k=10, where=None):
                if collection == "papers":
                    return [Hit(i) for i in self.ids[:top_k]]
                return []

        db = SQLiteStore(tmp_path / "test.db")
        db.init_schema()
        kb = KBManager(db, FakeVectorStore())
        asyncio.run(kb.upsert_paper({
            "id": "p1",
            "title": "Stereo Matching Test",
            "abstract": "cost volume attention",
        }))
        results = asyncio.run(kb.search_related("cost volume", 3))
        assert len(results) == 1
        assert results[0]["paper"]["id"] == "p1"

    def test_upsert_preserves_uploaded_fulltext_status(self, tmp_path):
        import asyncio
        from src.knowledge.kb_manager import KBManager

        class FakeVectorStore:
            def add_documents(self, collection, docs):
                pass

        db = SQLiteStore(tmp_path / "test.db")
        db.init_schema()
        db.execute(
            """INSERT INTO papers (id, title, retrieval_status, fulltext_path)
               VALUES (?,?,?,?)""",
            ("p:uploaded", "Uploaded Fulltext Paper", "downloaded", "papers/manual/paper.pdf"),
        )
        kb = KBManager(db, FakeVectorStore())

        asyncio.run(kb.upsert_paper({
            "id": "p:uploaded",
            "title": "Uploaded Fulltext Paper",
            "retrieval_status": "metadata_only",
            "abstract": "new external metadata",
        }))

        row = db.fetchone("SELECT retrieval_status, fulltext_path FROM papers WHERE id=?", ("p:uploaded",))
        assert row["retrieval_status"] == "downloaded"
        assert row["fulltext_path"] == "papers/manual/paper.pdf"

        db.execute(
            "UPDATE papers SET retrieval_status='missing_fulltext' WHERE id=?",
            ("p:uploaded",),
        )
        asyncio.run(kb.upsert_paper({
            "id": "p:uploaded",
            "title": "Uploaded Fulltext Paper",
            "retrieval_status": "metadata_only",
        }))
        row = db.fetchone("SELECT retrieval_status, fulltext_path FROM papers WHERE id=?", ("p:uploaded",))
        assert row["retrieval_status"] == "downloaded"


class TestParseAgent:
    def test_local_pdf_parse_updates_paper_status(self, tmp_path, monkeypatch):
        import asyncio
        from src.agents.base import AgentContext
        from src.agents.parse_agent import ParseAgent

        db = SQLiteStore(tmp_path / "test.db")
        db.init_schema()
        pdf_path = tmp_path / "papers" / "manual" / "paper.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(b"%PDF-1.4\n")
        db.execute(
            """INSERT INTO papers (id, title, retrieval_status, fulltext_path)
               VALUES (?,?,?,?)""",
            ("p:local", "Local PDF Paper", "downloaded", "papers/manual/paper.pdf"),
        )
        monkeypatch.setattr(
            "src.parsing.pymupdf_parser.extract_markdown_like_text",
            lambda path: "# Parsed Local PDF\ncontent",
        )
        ctx = AgentContext(workspace_id="ws", session_id="s", workspace_root=tmp_path, config={})

        result = asyncio.run(ParseAgent(db=db).run(ctx, {
            "papers": [{
                "id": "p:local",
                "title": "Local PDF Paper",
                "retrieval_status": "downloaded",
                "local_pdf_path": "papers/manual/paper.pdf",
            }],
            "deep_parse_top_k": 1,
        }))

        assert result.data["parsed"] == ["p:local"]
        row = db.fetchone("SELECT retrieval_status, fulltext_path, parsed_markdown_path FROM papers WHERE id=?", ("p:local",))
        assert row["retrieval_status"] == "parsed"
        assert row["fulltext_path"] == "papers/manual/paper.pdf"
        assert Path(row["parsed_markdown_path"]).exists()

    def test_infer_public_pdf_url_from_preprint_links(self):
        from src.agents.parse_agent import ParseAgent

        pa = ParseAgent()

        assert pa._infer_pdf_url({"url": "https://arxiv.org/abs/2501.08643"}) == "https://arxiv.org/pdf/2501.08643.pdf"
        assert pa._infer_pdf_url({"url": "https://openaccess.thecvf.com/content/CVPR2024/papers/X_Test_CVPR_2024_paper.pdf"}).endswith(".pdf")
        assert pa._infer_pdf_url({"url": "https://ieeexplore.ieee.org/abstract/document/9340849"}) is None


class TestVectorStore:
    def test_siliconflow_embedding_collection_get_or_create(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
        from src.knowledge.vector_store import VectorStore
        from src.llm.siliconflow_embedding import siliconflow_embedding_fn

        vs = VectorStore(tmp_path / "chroma", siliconflow_embedding_fn())
        first = vs._get_or_create("papers")
        second = vs._get_or_create("papers")
        assert first.name == "papers"
        assert second.name == "papers"

    def test_siliconflow_embedding_supports_query_embedding(self, monkeypatch):
        import src.llm.siliconflow_embedding as sf

        class FakeEmbeddings:
            def create(self, model, input):
                class Resp:
                    data = [type("Embedding", (), {"embedding": [float(len(t)), 0.0]}) for t in input]
                return Resp()

        class FakeClient:
            embeddings = FakeEmbeddings()

        monkeypatch.setattr(sf, "get_siliconflow_client", lambda: FakeClient())
        embed_fn = sf.siliconflow_embedding_fn()

        assert embed_fn.embed_query(["abc"])[0] == [3.0, 0.0]






class TestSelectedPaperContext:
    def test_selected_papers_are_included_in_search_results(self, tmp_path, monkeypatch):
        import asyncio
        import src.tools.search_tools as search_tools
        from src.agents.orchestrator import Orchestrator

        async def no_external_results(*args, **kwargs):
            return []

        class EmptyKB:
            async def search_related(self, *args, **kwargs):
                return []

        db = SQLiteStore(tmp_path / "test.db")
        db.init_schema()
        db.execute(
            """INSERT INTO papers (id, title, authors_json, abstract, retrieval_status)
               VALUES (?,?,?,?,?)""",
            ("p:selected", "Selected Stereo Paper", '["A"]', "selected abstract", "metadata_only"),
        )
        monkeypatch.setattr(search_tools, "search_all", no_external_results)
        orch = Orchestrator(db, None, None, EmptyKB())

        result = asyncio.run(orch._search(
            "continue original question",
            {"search_queries": ["stereo matching"]},
            {},
            ["p:selected"],
        ))

        assert result[0]["id"] == "p:selected"
        assert result[0]["source"] == "selected_library"

    def test_parse_budget_respects_configured_cap_for_selected_papers(self):
        from src.agents.orchestrator import Orchestrator

        orch = Orchestrator(None, None, None, None)
        papers = [
            {"id": f"p{i}", "source": "selected_library", "open_access_pdf_url": f"https://arxiv.org/pdf/{i}.pdf"}
            for i in range(18)
        ] + [{"id": f"m{i}", "source": "semantic_scholar"} for i in range(10)]

        assert orch._parse_budget(papers, 10, {"search": {"deep_parse_max_k": 30}}) == 10


class TestDesktopApp:
    def test_desktop_settings_route_registered(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws")
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})
        assert client.get("/api/settings").status_code == 200

    def test_cancel_not_running_task(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_cancel")
        with TestClient(app, headers={"X-Reader-Client": "desktop"}) as client:
            resp = client.post("/api/tasks/not-running/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_running"

    def test_papers_api_returns_metadata_not_workspace_files(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_papers")
        app.state.db.execute(
            """INSERT INTO papers (id, title, authors_json, year, venue, abstract, retrieval_status)
               VALUES (?,?,?,?,?,?,?)""",
            ("p:test", "Readable Stereo Paper", '["Alice", "Bob"]', 2025, "CVPR", "abstract", "parsed"),
        )
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})
        resp = client.get("/api/papers", params={"q": "Readable Stereo"})
        assert resp.status_code == 200
        papers = resp.json()["papers"]
        assert len(papers) == 1
        assert papers[0]["title"] == "Readable Stereo Paper"
        assert papers[0]["authors"] == ["Alice", "Bob"]

        detail = client.get("/api/papers/detail", params={"paper_id": "p:test"})
        assert detail.status_code == 200
        assert detail.json()["paper"]["retrieval_status"] == "parsed"

    def test_papers_api_hides_off_topic_by_default(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_off_topic")
        app.state.db.execute(
            "INSERT INTO papers (id, title, retrieval_status) VALUES (?,?,?)",
            ("p:off", "Pure Monocular Depth Estimation", "off_topic"),
        )
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})
        assert all(p["id"] != "p:off" for p in client.get("/api/papers").json()["papers"])
        shown = client.get("/api/papers", params={"include_off_topic": True}).json()["papers"]
        assert any(p["id"] == "p:off" for p in shown)

    def test_papers_status_bulk_update(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_bulk_status")
        app.state.db.execute(
            "INSERT INTO papers (id, title, retrieval_status) VALUES (?,?,?)",
            ("p:bulk", "Bulk Hidden Paper", "metadata_only"),
        )
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})
        resp = client.post("/api/papers/status", json={
            "paper_ids": ["p:bulk"],
            "status": "off_topic",
            "missing_reason": "test",
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1
        assert client.get("/api/papers", params={"q": "Bulk Hidden"}).json()["papers"] == []

    def test_papers_status_filter_returns_missing_fulltext(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_missing_filter")
        app.state.db.execute(
            "INSERT INTO papers (id, title, retrieval_status) VALUES (?,?,?)",
            ("p:missing", "Missing Fulltext Paper", "missing_fulltext"),
        )
        app.state.db.execute(
            "INSERT INTO papers (id, title, retrieval_status) VALUES (?,?,?)",
            ("p:parsed", "Parsed Paper", "parsed"),
        )
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})

        resp = client.get("/api/papers", params={"status": "missing_fulltext"})
        assert resp.status_code == 200
        papers = resp.json()["papers"]
        assert [p["id"] for p in papers] == ["p:missing"]

    def test_upload_paper_fulltext_binds_pdf_to_paper(self, tmp_path):
        import io
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_fulltext_upload")
        app.state.db.execute(
            "INSERT INTO papers (id, title, retrieval_status, missing_reason) VALUES (?,?,?,?)",
            ("p:upload", "Upload Target Paper", "missing_fulltext", "no_open_fulltext"),
        )
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})

        resp = client.post(
            "/api/papers/p:upload/fulltext",
            files={"file": ("paper.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["paper"]["retrieval_status"] == "downloaded"
        assert data["paper"]["missing_reason"] is None
        fulltext_path = data["paper"]["fulltext_path"]
        assert fulltext_path.endswith("paper.pdf")
        assert (app.state.workspace_root / fulltext_path).exists()

    def test_papers_api_repairs_missing_status_when_fulltext_exists(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_fulltext_repair")
        app.state.db.execute(
            """INSERT INTO papers (id, title, retrieval_status, fulltext_path)
               VALUES (?,?,?,?)""",
            ("p:repair", "Already Uploaded Paper", "missing_fulltext", "papers/manual/paper.pdf"),
        )
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})

        missing = client.get("/api/papers", params={"status": "missing_fulltext"}).json()["papers"]
        assert all(p["id"] != "p:repair" for p in missing)
        row = app.state.db.fetchone("SELECT retrieval_status FROM papers WHERE id=?", ("p:repair",))
        assert row["retrieval_status"] == "downloaded"

    def test_import_file_creates_selectable_paper(self, tmp_path):
        import io
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_import_pdf")
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})

        resp = client.post(
            "/api/files/import",
            files={"file": ("uploaded paper.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "imported"
        assert data["paper"]["retrieval_status"] == "downloaded"
        assert data["paper"]["fulltext_path"].endswith("uploaded paper.pdf")
        row = app.state.db.fetchone("SELECT * FROM papers WHERE id=?", (data["paper"]["id"],))
        assert row["fulltext_path"] == data["paper"]["fulltext_path"]

    def test_report_rename_and_delete_updates_gap_analysis_path(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_reports_manage")
        reports_dir = app.state.workspace_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        old_path = reports_dir / "old_report.md"
        old_path.write_text("# Old", encoding="utf-8")
        app.state.db.execute(
            """INSERT INTO gap_analyses (id, session_id, direction, report_path)
               VALUES (?,?,?,?)""",
            ("g:report", "s", "direction", str(old_path)),
        )
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})

        renamed = client.patch("/api/reports/rename", json={
            "path": "reports/old_report.md",
            "new_name": "renamed_report",
        })
        assert renamed.status_code == 200
        assert renamed.json()["path"] == "reports/renamed_report.md"
        new_path = reports_dir / "renamed_report.md"
        assert not old_path.exists()
        assert new_path.exists()
        row = app.state.db.fetchone("SELECT report_path FROM gap_analyses WHERE id=?", ("g:report",))
        assert row["report_path"] == str(new_path.resolve())

        deleted = client.delete("/api/reports", params={"path": "reports/renamed_report.md"})
        assert deleted.status_code == 200
        assert not new_path.exists()
        row = app.state.db.fetchone("SELECT report_path FROM gap_analyses WHERE id=?", ("g:report",))
        assert row["report_path"] is None

    def test_report_chat_uses_existing_report_without_generating_new_report(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        class FakeLLM:
            def __init__(self):
                self.messages = None

            async def chat_stream(self, messages, model=None):
                self.messages = messages
                yield "结论：这份报告证据不足，需要补全文。"

        app, _ = _build_app(tmp_path / "desktop_ws_report_chat")
        reports_dir = app.state.workspace_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "existing.md"
        report_path.write_text("# 测试报告\n\n候选空白：证据不足。", encoding="utf-8")
        before_reports = sorted(p.name for p in reports_dir.glob("*.md"))
        fake_llm = FakeLLM()
        app.state.llm = fake_llm
        app.state.db.execute(
            "INSERT INTO sessions (id, workspace_id, title, status) VALUES (?,?,?,?)",
            ("s:report-chat", app.state.workspace_id, "已有上下文", "idle"),
        )
        app.state.db.execute(
            "INSERT INTO messages (id, session_id, role, content) VALUES (?,?,?,?)",
            ("m:ctx-user", "s:report-chat", "user", "上一问：单目深度先验怎样融合进 stereo matching？"),
        )
        app.state.db.execute(
            "INSERT INTO messages (id, session_id, role, content) VALUES (?,?,?,?)",
            ("m:ctx-assistant", "s:report-chat", "assistant", "上一答：重点关注不确定性门控和特征级融合。"),
        )
        app.state.db.execute(
            """INSERT INTO gap_analyses (id, session_id, direction, report_path)
               VALUES (?,?,?,?)""",
            ("g:ctx-report", "s:report-chat", "单目先验融合", str(report_path.resolve())),
        )
        parsed_dir = app.state.workspace_root / "parsed"
        parsed_dir.mkdir(parents=True, exist_ok=True)
        parsed_path = parsed_dir / "selected_paper.md"
        parsed_path.write_text("# Selected Paper\n\n这篇论文提出 uncertainty gated monodepth prior fusion。", encoding="utf-8")
        app.state.db.execute(
            """INSERT INTO papers (id, title, authors_json, abstract, retrieval_status, parsed_markdown_path)
               VALUES (?,?,?,?,?,?)""",
            ("p:selected-file", "Selected Monodepth Stereo Paper", '["Alice"]',
             "Uses monocular depth priors for stereo matching.", "parsed", str(parsed_path.resolve())),
        )

        import asyncio
        from types import SimpleNamespace
        from src.server.routes_chat import ChatRequest, _run_report_chat
        req = ChatRequest(session_id="s:report-chat", mode="report_chat", selected_paper_ids=["p:selected-file"], message="这个报告最主要的问题是什么？")
        asyncio.run(_run_report_chat(req, SimpleNamespace(app=app), req.session_id, "m:new"))
        data = {"session_id": req.session_id, "report_path": str(report_path.resolve())}
        answer = app.state.db.fetchone("SELECT content FROM messages WHERE session_id=? AND role='assistant' ORDER BY rowid DESC", (req.session_id,))
        assert "证据不足" in answer["content"]
        assert sorted(p.name for p in reports_dir.glob("*.md")) == before_reports
        assert data["report_path"] == str(report_path.resolve())
        assert [r["id"] for r in app.state.db.fetchall("SELECT id FROM gap_analyses")] == ["g:ctx-report"]
        rows = app.state.db.fetchall(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY created_at",
            (data["session_id"],),
        )
        assert [r["role"] for r in rows][-2:] == ["user", "assistant"]
        assert "测试报告" in fake_llm.messages[1]["content"]
        assert "上一问：单目深度先验怎样融合" in fake_llm.messages[1]["content"]
        assert "上一答：重点关注不确定性门控" in fake_llm.messages[1]["content"]
        assert "Selected Monodepth Stereo Paper" in fake_llm.messages[1]["content"]
        assert "uncertainty gated monodepth prior fusion" in fake_llm.messages[1]["content"]
        fresh = ChatRequest(session_id='s:new-chat-title', mode='report_chat', message='如何补充论文全文？')
        asyncio.run(_run_report_chat(fresh, SimpleNamespace(app=app), fresh.session_id, 'm:fresh'))
        assert app.state.db.fetchone('SELECT title FROM sessions WHERE id=?', (fresh.session_id,))['title'] == '只对话: 如何补充论文全文？'
        assert app.state.db.fetchone('SELECT title FROM sessions WHERE id=?', (req.session_id,))['title'] == '已有上下文'

    def test_report_chat_rejects_paths_outside_reports(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_report_chat_safety")
        (app.state.workspace_root / "notes").mkdir(parents=True, exist_ok=True)
        (app.state.workspace_root / "notes" / "secret.md").write_text("secret", encoding="utf-8")
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})

        resp = client.post("/api/chat", json={
            "mode": "report_chat",
            "report_path": "notes/secret.md",
            "message": "读这个",
        })

        assert resp.status_code == 403
        assert app.state.db.fetchone("SELECT id FROM sessions") is None

    def test_message_edit_and_delete_turn(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_message_ops")
        app.state.db.execute(
            "INSERT INTO sessions (id, workspace_id, title) VALUES (?,?,?)",
            ("s:ops", app.state.workspace_id, "Message Ops"),
        )
        for mid, role, content in [
            ("m:user1", "user", "old question"),
            ("m:assistant1", "assistant", "old answer"),
            ("m:user2", "user", "keep question"),
            ("m:assistant2", "assistant", "keep answer"),
        ]:
            app.state.db.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?,?,?,?)",
                (mid, "s:ops", role, content),
            )
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})

        edited = client.patch("/api/messages/m:user1", json={"content": "new question"})
        assert edited.status_code == 200
        assert edited.json()["message"]["content"] == "new question"

        deleted = client.delete("/api/messages/m:assistant1/turn")
        assert deleted.status_code == 200
        assert deleted.json()["deleted_message_ids"] == ["m:user1", "m:assistant1"]
        rows = app.state.db.fetchall(
            "SELECT id FROM messages WHERE session_id=? ORDER BY rowid",
            ("s:ops",),
        )
        assert [r["id"] for r in rows] == ["m:user2", "m:assistant2"]

    def test_gpt_handoff_export_contains_report_conversation_and_papers(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_gpt_handoff")
        ws = app.state.workspace_root
        reports_dir = ws / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "gap_report.md"
        report_path.write_text("# 原报告\n\n候选空白：原始结论。", encoding="utf-8")
        parsed_dir = ws / "parsed"
        parsed_dir.mkdir(parents=True, exist_ok=True)
        parsed_path = parsed_dir / "paper.md"
        parsed_path.write_text("# Paper\n\nThis paper uses monodepth priors for stereo matching.", encoding="utf-8")
        app.state.db.execute(
            "INSERT INTO sessions (id, workspace_id, title) VALUES (?,?,?)",
            ("s:gpt", app.state.workspace_id, "GPT Export"),
        )
        app.state.db.execute(
            "INSERT INTO messages (id, session_id, role, content) VALUES (?,?,?,?)",
            ("m:gpt:user", "s:gpt", "user", "我不满意这个空白，请重新判断。"),
        )
        app.state.db.execute(
            """INSERT INTO papers (id, title, authors_json, abstract, retrieval_status, parsed_markdown_path)
               VALUES (?,?,?,?,?,?)""",
            ("p:gpt", "GPT Handoff Paper", '["Alice"]', "abstract", "parsed", str(parsed_path.resolve())),
        )
        app.state.db.execute(
            """INSERT INTO gap_analyses (id, session_id, direction, search_log_json,
                   matrix_json, gaps_json, evidence_json, report_path)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                "g:gpt", "s:gpt", '{"method_category":"mono prior"}',
                '{"papers":[{"id":"p:gpt","title":"GPT Handoff Paper"}]}',
                '{}', '[{"name":"old gap"}]', '[{"paper_id":"p:gpt","quote":"evidence"}]',
                str(report_path.resolve()),
            ),
        )
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})

        resp = client.post("/api/export/gpt-handoff", json={
            "session_id": "s:gpt",
            "report_path": "reports/gap_report.md",
            "selected_paper_ids": ["p:gpt"],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "exported"
        out = ws / data["path"]
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "研究复核资料包" in content
        assert "我不满意这个空白" in content
        assert "# 原报告" in content
        assert "GPT Handoff Paper" in content
        assert "monodepth priors for stereo matching" in content
        assert "请你不要直接复述原报告" in content

    def test_workspace_organize_creates_human_guide(self, tmp_path):
        from fastapi.testclient import TestClient
        from src.app.desktop import _build_app

        app, _ = _build_app(tmp_path / "desktop_ws_organize")
        client = TestClient(app, headers={"X-Reader-Client": "desktop"})

        resp = client.post("/api/workspace/organize")

        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "00_WORKSPACE_GUIDE.md"
        guide = app.state.workspace_root / data["path"]
        assert guide.exists()
        content = guide.read_text(encoding="utf-8")
        assert "Quick Map" in content
        assert "reports/" in content
        assert "exports/" in content
        assert "papers/parsed/" in content
        assert "Recommended Daily Workflow" in content


class TestToolRegistry:
    def test_permission_denied_blocks_tool(self):
        import asyncio
        from src.tools.registry import ToolRegistry, ToolSpec

        called = {"value": False}

        async def dangerous_tool():
            called["value"] = True
            return {"ok": True}

        reg = ToolRegistry()
        reg.register(dangerous_tool, ToolSpec(name="danger", permission="delete_file"))
        result = asyncio.run(reg.call("run1", "danger", {}))
        assert result["error"] == "permission_denied"
        assert called["value"] is False

    def test_permission_ask_blocks_tool_until_confirmed(self):
        import asyncio
        from src.tools.registry import ToolRegistry, ToolSpec

        reg = ToolRegistry()
        reg.register(lambda: {"ok": True}, ToolSpec(name="manual_note", permission="write_manual_notes"))
        result = asyncio.run(reg.call("run1", "manual_note", {}))
        assert result["error"] == "permission_required"


class TestMcpSearchMapping:
    def test_metadata_from_mcp(self):
        from src.tools.search_tools import _metadata_from_mcp

        meta = _metadata_from_mcp({
            "id": "abc",
            "title": "MCP Stereo Paper",
            "summary": "abstract",
            "pdf_url": "https://example.com/paper.pdf",
            "year": 2024,
        })
        assert meta is not None
        assert meta.id == "mcp:abc"
        assert meta.title == "MCP Stereo Paper"
        assert meta.open_access_pdf_url == "https://example.com/paper.pdf"
