import json, logging, uuid, asyncio, time, re
from datetime import datetime
from pathlib import Path
from src.agents.base import BaseAgent, AgentContext, AgentResult

logger = logging.getLogger(__name__)

# Ordered pipeline stages, used to report "stage X / N" progress to the UI.
PIPELINE_STAGES = [
    "DirectionParser", "SearchAgent", "ParseAgent", "InnovationExtractor",
    "AnalyzeAgent", "CriticAgent", "MonitorAgent", "ReportGenerator",
]

AGENT_LABELS = {
    "DirectionParser": "解析研究方向",
    "SearchAgent": "搜索相关论文",
    "ParseAgent": "解析论文全文",
    "InnovationExtractor": "提取创新画像",
    "AnalyzeAgent": "构建创新矩阵并推断空白",
    "CriticAgent": "检查过度推断",
    "MonitorAgent": "检查缺全文论文",
    "ReportGenerator": "生成报告",
}


class Orchestrator(BaseAgent):
    name = "Orchestrator"

    def __init__(self, db, llm, vs, kb, ws_manager=None):
        self.db = db
        self.llm = llm
        self.vs = vs
        self.kb = kb
        self.ws_manager = ws_manager

    async def _notify(self, session_id: str, event: dict):
        if self.ws_manager:
            try:
                await self.ws_manager.broadcast(session_id, event)
            except Exception:
                pass

    async def _progress(self, session_id: str, agent: str, stage: str,
                        message: str, detail: dict | None = None,
                        elapsed_ms: int | None = None):
        await self._notify(session_id, {
            "type": "agent_progress", "session_id": session_id,
            "agent": agent, "stage": stage,
            "message": message, "detail": detail or {},
            "elapsed_ms": elapsed_ms,
        })

    async def _create_agent_run(self, session_id: str, agent_name: str,
                                input_data: dict, message: str | None = None) -> str:
        run_id = str(uuid.uuid4())
        self.db.execute(
            """INSERT INTO agent_runs (id, session_id, agent_name, status,
               input_json, started_at) VALUES (?,?,?,?,?,?)""",
            (run_id, session_id, agent_name, "running",
             json.dumps(input_data, ensure_ascii=False),
             datetime.now().isoformat()),
        )
        stage_index = PIPELINE_STAGES.index(agent_name) + 1 if agent_name in PIPELINE_STAGES else 0
        total_stages = len(PIPELINE_STAGES)
        await self._notify(session_id, {
            "type": "agent_started", "session_id": session_id,
            "agent": agent_name, "run_id": run_id,
            "message": message or AGENT_LABELS.get(agent_name, f"{agent_name} 开始"),
            "stage_index": stage_index, "total_stages": total_stages,
        })
        return run_id

    async def _finish_agent_run(self, run_id: str, agent_name: str,
                                session_id: str, result: AgentResult):
        self.db.execute(
            """UPDATE agent_runs SET status=?, output_json=?, error=?,
               finished_at=? WHERE id=?""",
            (result.status,
             json.dumps(result.data, ensure_ascii=False),
             result.error,
             datetime.now().isoformat(),
             run_id),
        )
        await self._notify(session_id, {
            "type": "agent_completed" if result.status == "completed" else "agent_failed",
            "session_id": session_id, "agent": agent_name,
            "data": result.data, "error": result.error,
        })

    async def run(self, ctx: AgentContext,
                  input_data: dict) -> AgentResult:
        user_message = input_data.get("message", "")
        selected_paper_ids = [pid for pid in input_data.get("selected_paper_ids", []) if isinstance(pid, str)]
        user_message_id = input_data.get("message_id") or str(uuid.uuid4())
        session_id = ctx.session_id or str(uuid.uuid4())

        existing = self.db.fetchone(
            "SELECT id FROM sessions WHERE id=?", (session_id,)
        )
        if not existing:
            self.db.execute(
                """INSERT INTO sessions (id, workspace_id, title, status)
                   VALUES (?,?,?,?)""",
                (session_id, ctx.workspace_id, user_message[:80], "busy"),
            )
        self.db.execute(
            """INSERT OR IGNORE INTO messages (id, session_id, role, content)
               VALUES (?,?,?,?)""",
            (user_message_id, session_id, "user", user_message),
        )

        previous_direction = self._latest_session_direction(session_id)
        recent_context = self._recent_direction_context(
            session_id, exclude_message_id=user_message_id
        )
        research_context = self._latest_research_context(session_id)
        if research_context:
            recent_context = research_context + "\n最近对话：\n" + recent_context

        try:
            t0 = time.time()
            await self._progress(session_id, "Orchestrator", "workflow_started",
                                 "开始分析研究方向",
                                 {"message": user_message, "selected_papers": len(selected_paper_ids),
                                  "regular_model":getattr(self.llm,'fast_model',None),
                                  "analysis_model":getattr(self.llm,'reasoning_model',None),
                                  "regular_effort":getattr(self.llm,'regular_effort',None),
                                  "analysis_effort":getattr(self.llm,'analysis_effort',None)})

            # Step 1: Parse direction
            run1 = await self._create_agent_run(session_id, "DirectionParser", {
                "message": user_message,
                "has_previous_direction": bool(previous_direction),
                "has_recent_context": bool(recent_context),
            })
            await self._progress(session_id, "DirectionParser", "direction_parse_started", "正在解析研究方向")
            direction = await self._parse_direction(
                user_message,
                previous_direction=previous_direction,
                recent_context=recent_context,
            )
            if not self._direction_is_usable(direction):
                raise ValueError(
                    "无法从当前消息恢复出明确的研究方向。"
                    "请写明要研究的任务、方法、实现差异或待核查主张，或在原分析会话中继续。"
                )
            await self._finish_agent_run(run1, "DirectionParser", session_id,
                                         AgentResult(status="completed", data=direction))
            t1 = time.time()
            await self._progress(session_id, "DirectionParser", "direction_parse_completed",
                                 "方向解析完成",
                                 {"domain": direction.get("research_domain", ""),
                                  "task": direction.get("target_task", ""),
                                  "stage": direction.get("method_component",""),
                                  "method": direction.get("method_subcategory",""),
                                  "queries": direction.get("search_queries", [])[:5]},
                                 elapsed_ms=int((t1-t0)*1000))

            # Step 2: Search
            run2 = await self._create_agent_run(session_id, "SearchAgent", {
                "direction": direction,
                "selected_paper_ids": selected_paper_ids,
            })
            await self._progress(session_id, "SearchAgent", "search_started",
                                 "开始搜索论文",
                                 {"queries": direction.get("search_queries", [])[:5],
                                  "selected_papers": len(selected_paper_ids)})
            inherited_paper_ids = self._session_paper_ids(session_id)
            research_paper_ids = list(dict.fromkeys(selected_paper_ids + inherited_paper_ids))
            papers = await self._search(user_message, direction, ctx.config, research_paper_ids)
            await self._finish_agent_run(run2, "SearchAgent", session_id,
                                         AgentResult(status="completed", data={"count": len(papers)}))
            t2 = time.time()
            await self._progress(session_id, "SearchAgent", "search_completed",
                                 f"搜索完成，找到 {len(papers)} 篇论文",
                                 {"count": len(papers), "sources": list(set(p.get("source","") for p in papers))},
                                 elapsed_ms=int((t2-t1)*1000))

            # Save papers
            for p in papers:
                try:
                    await self.kb.upsert_paper(p)
                except Exception as e:
                    logger.warning(f"Failed to save paper {p.get('id')}: {e}")

            # Step 3: Parse
            run3 = await self._create_agent_run(session_id, "ParseAgent",
                                                 {"paper_count": len(papers)})
            top_k = self._parse_budget(papers, ctx.config.get("search", {}).get("deep_parse_top_k", 50), ctx.config)
            await self._progress(session_id, "ParseAgent", "parse_started",
                                 f"本轮共 {len(papers)} 篇资料，尝试获取/解析其中 {min(top_k, len(papers))} 篇全文；其余逐篇列为待处理")
            from src.agents.parse_agent import ParseAgent
            async def parse_progress(detail):
                await self._progress(session_id, 'ParseAgent', 'paper_progress',
                                     f"全文处理 {detail['completed']}/{detail['total']}：{detail['title']}", detail)
            pa = ParseAgent(db=self.db, progress=parse_progress)
            parse_result = await pa.run(ctx, {
                "papers": papers,
                "deep_parse_top_k": top_k,
            })
            await self._finish_agent_run(run3, "ParseAgent", session_id, parse_result)
            pd = parse_result.data
            t3 = time.time()
            await self._progress(session_id, "ParseAgent", "parse_completed",
                                 f"解析完成：{len(pd.get('parsed',[]))} 篇全文，{len(pd.get('metadata_only',[]))} 篇仅元数据，{len(pd.get('failed',[]))} 篇失败",
                                 {"parsed": len(pd.get("parsed",[])),
                                  "metadata_only": len(pd.get("metadata_only",[])),
                                  "failed": len(pd.get("failed",[]))},
                                 elapsed_ms=int((t3-t2)*1000))

            # Step 4: Extract innovations
            run4 = await self._create_agent_run(session_id, "InnovationExtractor",
                                                 {"parsed": parse_result.data.get("parsed", []),
                                                  "metadata_only": parse_result.data.get("metadata_only", [])})
            await self._progress(session_id, "InnovationExtractor", "innovation_extraction_started",
                                 f"开始提取创新画像", {"total": len(parse_result.data.get("parsed",[])) + len(parse_result.data.get("metadata_only",[]))})
            innovations = await self._extract_innovations(ctx, parse_result, papers)
            extracted_count = sum(not item.get('error') for item in innovations)
            for i in innovations:
                pid = i.get("paper_id")
                if pid and not i.get('error'):
                    try:
                        await self.kb.save_innovation_profile(pid, i)
                    except Exception as e:
                        logger.warning(f"Failed to save profile {pid}: {e}")
            await self._finish_agent_run(run4, "InnovationExtractor", session_id,
                                         AgentResult(status="completed",
                                                     data={"count": extracted_count, "failed":len(innovations)-extracted_count}))
            t4 = time.time()
            await self._progress(session_id, "InnovationExtractor", "innovation_extraction_completed",
                                 f"创新画像提取完成：成功 {extracted_count} 篇，失败 {len(innovations)-extracted_count} 篇",
                                 {"count": extracted_count},
                                 elapsed_ms=int((t4-t3)*1000))

            # Step 5: Gap analysis (with empty guard)
            run5 = await self._create_agent_run(session_id, "AnalyzeAgent",
                                                 {"innovation_count": len(innovations)})
            await self._progress(session_id, "AnalyzeAgent", "gap_analysis_started",
                                 "正在构建创新矩阵并推断空白",
                                 {"innovation_profiles": len(innovations)})
            if not innovations:
                analysis = {
                    "gaps": [], "matrix": {},
                    "warning": "No innovation profiles extracted; gap analysis is not reliable."
                }
                t5 = time.time()
                await self._progress(session_id, "AnalyzeAgent", "gap_analysis_completed",
                                     "创新画像为空，无法可靠推断空白", {"gap_count": 0},
                                     elapsed_ms=int((t5-t4)*1000))
            else:
                analysis = await self._analyze_gaps(direction, innovations)
                analysis = self._normalize_analysis(analysis)
                analysis = self._enforce_analysis_reliability(
                    direction, innovations, analysis
                )
                gcnt = len(analysis.get("gaps", []))
                t5 = time.time()
                await self._progress(session_id, "AnalyzeAgent", "gap_analysis_completed",
                                     f"空白分析完成，发现 {gcnt} 个候选空白",
                                     {"gap_count": gcnt,
                                      "matrix_axes": analysis.get("matrix", {}).get("axes", []) if isinstance(analysis.get("matrix"), dict) else []},
                                     elapsed_ms=int((t5-t4)*1000))
            from src.analysis.research_completion import complete_research
            papers, innovations, analysis, pd = await complete_research(
                self, ctx, direction, papers, innovations, analysis, pd)
            extracted_count = sum(not item.get('error') for item in innovations)
            from src.analysis.paper_ledger import paper_ledger
            analysis['paper_processing'] = paper_ledger(papers, innovations, analysis.get('coverage_audit', []), pd)
            analysis['inherited_paper_ids'] = inherited_paper_ids
            unfinished = [row for row in analysis['paper_processing'] if row['status'] in {'not_analyzed','extraction_failed','unassessed'}]
            if unfinished:
                warning=f"本轮有 {len(unfinished)} 篇尚未完成画像或证据审计；结论仅针对已核实的资料，不能宣称已覆盖全部输入。"
                analysis.setdefault('reliability', {}).setdefault('warnings', []).append(warning)
            await self._finish_agent_run(run5, "AnalyzeAgent", session_id,
                                          AgentResult(status="completed",
                                                      data={"gap_count": len(analysis.get("gaps", [])), "paper_processing":analysis['paper_processing'], "reliability":analysis.get('reliability',{})}))

            # Step 6: Critic check
            run6 = await self._create_agent_run(session_id, "CriticAgent",
                                                 {"gap_count": len(analysis.get("gaps", []))})
            await self._progress(session_id, "CriticAgent", "critic_started",
                                 "正在检查空白分析是否过度推断")
            review_candidates = analysis.get('gaps', []) + analysis.get('provisional_gaps', [])
            if review_candidates:
                from src.agents.critic_agent import CriticAgent
                critic_result = await CriticAgent(self.llm).run(ctx, {
                    "analysis": dict(analysis, gaps=review_candidates),
                    "innovations": innovations,
                })
            else:
                critic_result = AgentResult(status="completed", data={
                    "skipped": True,
                    "warnings": ["没有通过可靠性门的候选空白，已跳过额外 LLM 评审。"],
                    "suggestions": ["补全研究方向或创新矩阵后重新运行分析。"],
                })
            if critic_result.status == "completed":
                critic_data = self._normalize_json_object(critic_result.data, "warnings")
                analysis["critic"] = critic_data
                analysis = self._apply_critic_verdicts(
                    analysis, critic_data, innovations
                )
            await self._finish_agent_run(run6, "CriticAgent", session_id, critic_result)
            await self._progress(session_id, "CriticAgent", "critic_completed",
                                 "没有正式候选，已跳过额外模型复核" if (analysis.get('critic') or {}).get('skipped') else "过度推断检查完成",
                                 {"warnings": len((analysis.get("critic") or {}).get("warnings", [])),
                                  "accepted_gaps": len(analysis.get("gaps", [])),
                                  "provisional_gaps": len(analysis.get("provisional_gaps", [])),
                                  "rejected_gaps": len(analysis.get("rejected_gaps", []))})

            # Step 7: Monitor check
            run7 = await self._create_agent_run(session_id, "MonitorAgent",
                                                  {"paper_count": len(papers)})
            from src.agents.monitor_agent import MonitorAgent
            monitor_result = await MonitorAgent(db=self.db).run(ctx, {"papers": papers})
            missing = monitor_result.data.get("papers", []) if monitor_result.status == "completed" else []
            await self._finish_agent_run(run7, "MonitorAgent", session_id,
                                         monitor_result)
            if missing:
                await self._progress(session_id, "MonitorAgent", "missing_found",
                                     f"发现 {len(missing)} 篇缺全文论文",
                                     {"missing_count": len(missing)},
                                     elapsed_ms=int((time.time()-t5)*1000) if 't5' in dir() else None)

            # Step 8: Generate report
            run8 = await self._create_agent_run(session_id, "ReportGenerator", {})
            from src.analysis.reader_report import write_reader_report
            snapshot_dir=ctx.workspace_root/'.agent_history'/'research'
            snapshot_dir.mkdir(parents=True,exist_ok=True)
            snapshot_path=snapshot_dir/(run8+'.json')
            def save_snapshot():
                snapshot_path.write_text(json.dumps({'direction':direction,'papers':papers,
                    'innovations':innovations,'analysis':analysis},ensure_ascii=False,indent=2),encoding='utf-8')
            save_snapshot()
            try:
                analysis['reader_report'] = await write_reader_report(
                    self.llm, direction, papers, innovations, analysis)
            finally:
                save_snapshot()
            report_path = await self._generate_report(
                ctx, direction, papers, innovations, analysis, missing,
                selected_paper_ids=selected_paper_ids,
                title_hint=user_message,
            )
            report_content = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
            await self._finish_agent_run(run8, "ReportGenerator", session_id,
                                          AgentResult(status="completed",
                                                      data={"path": str(report_path)}))
            evidence = self._collect_evidence(innovations)
            self._save_gap_analysis(session_id, direction, papers, innovations,
                                    analysis, evidence, report_path,
                                    selected_paper_ids=selected_paper_ids)

            self.db.execute(
                "UPDATE sessions SET status='idle', updated_at=datetime('now') WHERE id=?",
                (session_id,),
            )

            # Save assistant message as a compact summary + structured metadata,
            # so the UI can render a report card instead of dumping the full
            # Markdown report into the chat transcript.
            gaps_count = len((analysis or {}).get("gaps", []))
            provisional_count = len((analysis or {}).get("provisional_gaps", []))
            try:
                rel_report_path = str(
                    report_path.resolve().relative_to(ctx.workspace_root.resolve())
                ).replace("\\", "/")
            except Exception:
                rel_report_path = str(report_path)
            from src.analysis.reader_report import report_summary
            conclusion = report_summary(report_content)
            summary_content = conclusion or f"本轮纳入 {len(papers)} 条资料，尚未完成分析 {len(unfinished)} 篇。详见报告：{rel_report_path}"
            assistant_message_id = str(uuid.uuid4())
            self.db.execute(
                """INSERT INTO messages (id, session_id, role, content, metadata_json)
                   VALUES (?,?,?,?,?)""",
                (assistant_message_id, session_id, "assistant", summary_content,
                 json.dumps({
                     "kind": "report",
                     "report_summary": conclusion,
                     "mode": "analysis",
                     "unprocessed_count": len(unfinished),
                     "report_path": rel_report_path,
                     "papers_found": len(papers),
                     "innovations_extracted": extracted_count,
                     "missing_count": len(missing),
                     "gaps_count": gaps_count,
                     "provisional_gaps_count": provisional_count,
                 }, ensure_ascii=False)),
            )

            return AgentResult(status="completed", data={
                "paper_processing": analysis['paper_processing'],
                "unprocessed_count": len(unfinished),
                "report_path": str(report_path),
                "report_path_rel": rel_report_path,
                "report_summary": conclusion,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "papers_found": len(papers),
                "innovations_extracted": extracted_count,
                "missing_count": len(missing),
                "gaps_count": gaps_count,
                "provisional_gaps_count": provisional_count,
                "missing_papers": missing[:50],
                "evidence": evidence[:50],
                "report_content": report_content,
            })

        except Exception as e:
            logger.exception("Orchestrator failed")
            self.db.execute(
                """UPDATE agent_runs SET status='failed', error=?, finished_at=?
                   WHERE session_id=? AND status='running'""",
                (str(e), datetime.now().isoformat(), session_id),
            )
            self.db.execute(
                "UPDATE sessions SET status='idle', updated_at=datetime('now') WHERE id=?",
                (session_id,),
            )
            return AgentResult(status="failed", error=str(e))

    def _session_paper_ids(self, session_id: str) -> list[str]:
        row = self.db.fetchone(
            "SELECT search_log_json FROM gap_analyses WHERE session_id=? AND id NOT LIKE 'checkpoint_%' ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id,),
        ) if self.db and session_id else None
        try:
            data = json.loads((row or {}).get('search_log_json') or '{}')
            return list(dict.fromkeys(p['id'] for p in data.get('papers', []) if isinstance(p,dict) and isinstance(p.get('id'),str)))
        except (TypeError, ValueError, AttributeError):
            return []

    def _latest_session_direction(self, session_id: str) -> dict:
        if not self.db or not session_id:
            return {}
        row = self.db.fetchone(
            """SELECT direction FROM gap_analyses
               WHERE session_id=? AND id NOT LIKE 'checkpoint_%'
               ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (session_id,),
        )
        if not row or not row.get("direction"):
            return {}
        value = row.get("direction")
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _latest_research_context(self, session_id: str) -> str:
        """Expose structured candidate history that would be lost by report-prefix clipping."""
        if not self.db or not session_id:
            return ""
        row = self.db.fetchone(
            "SELECT gaps_json, search_log_json FROM gap_analyses WHERE session_id=? "
            "AND id NOT LIKE 'checkpoint_%' ORDER BY created_at DESC, rowid DESC LIMIT 1", (session_id,)
        )
        if not row:
            return ""
        try:
            log = json.loads(row.get('search_log_json') or '{}')
            groups = [('正式候选（仍需验证）', json.loads(row.get('gaps_json') or '[]')),
                      ('待验证候选', log.get('provisional_gaps') or []),
                      ('否决候选', log.get('rejected_gaps') or [])]
            summary = []
            for label, candidates in groups:
                for index, candidate in enumerate(candidates[:8], 1):
                    if not isinstance(candidate, dict):
                        continue
                    summary.append({'group': label, 'position': index, **{
                        key: candidate.get(key) for key in
                        ('gap_id', 'name', 'description', 'candidate_status', 'required_evidence', 'decisive_experiment')
                    }})
            return '上一轮研究候选与待办（历史判断，不是已证实事实）：\n' + json.dumps(summary, ensure_ascii=False)
        except (ValueError, TypeError, AttributeError):
            return ""

    def _recent_direction_context(self, session_id: str,
                                  exclude_message_id: str = "",
                                  limit: int = 8,
                                  max_chars: int = 6000) -> str:
        if not self.db or not session_id:
            return ""
        rows = self.db.fetchall(
            """SELECT role, content FROM messages
               WHERE session_id=? AND id<>?
               ORDER BY rowid DESC LIMIT ?""",
            (session_id, exclude_message_id, limit),
        )
        parts = []
        total = 0
        for row in reversed(rows):
            role = row.get("role") or "unknown"
            content = " ".join(str(row.get("content") or "").split())[:1200]
            if not content:
                continue
            line = f"{role}: {content}"
            remaining = max_chars - total
            if remaining <= 0:
                break
            parts.append(line[:remaining])
            total += len(parts[-1])
        return "\n".join(parts)

    def _is_follow_up_request(self, message: str) -> bool:
        text = " ".join(str(message or "").lower().split())
        markers = (
            "之前", "上次", "前面", "刚才", "原来", "原报告", "已有报告",
            "继续", "重新", "再生成", "再分析", "按照对话", "基于对话",
            "previous", "earlier", "above", "continue", "again", "regenerate",
        )
        return any(marker in text for marker in markers)

    def _query_has_research_focus(self, query: str) -> bool:
        text = " ".join(str(query or "").lower().split())
        if len(text) < 8:
            return False
        operational = (
            "重新生成报告", "按照之前对话", "再试一次", "继续运行",
            "regenerate report", "from the previous discussion", "run again",
        )
        return not any(marker in text for marker in operational)

    def _direction_is_usable(self, direction: dict) -> bool:
        if not isinstance(direction, dict):
            return False
        scalar_focus = any(
            str(direction.get(key) or "").strip()
            for key in ("target_task",
                        "method_component", "method_category", "method_subcategory")
        )
        detail_focus = any(
            str(item or "").strip()
            for key in ("implementation_details", "claims_to_verify")
            for item in direction.get(key, []) or []
        )
        query_focus = any(
            self._query_has_research_focus(query)
            for query in direction.get("search_queries", []) or []
        )
        question_focus = self._query_has_research_focus(direction.get('research_question', ''))
        return scalar_focus or detail_focus or query_focus or question_focus

    async def _parse_direction(self, message: str,
                               previous_direction: dict | None = None,
                               recent_context: str = "") -> dict:
        from src.parsing.schemas import DirectionParseResult
        previous_direction = DirectionParseResult(**(previous_direction or {})).model_dump() if previous_direction else {}
        follow_up = self._is_follow_up_request(message)
        result = await self.llm.chat_json([
            {"role": "system",
             "content": (
                 "你是论文研究方向解析助手。拆解研究想法为结构化字段。只输出合法JSON。"
                 "必须输出对象，字段包括 research_question, research_domain, target_task, "
                 "method_component, method_category, method_subcategory, "
                 "implementation_details, claims_to_verify, search_queries, matrix_axes_hint。"
                 "implementation_details/claims_to_verify/search_queries/matrix_axes_hint 必须是字符串数组；"
                 "未知时输出空数组 []，不要输出 null、空字符串或'无'。"
                 "claims_to_verify 用于保存用户输入中关于‘尚未有人做、现有方法只做了某事、某论文使用了某种机制’"
                 "等需要文献反证的事实或新颖性主张；这些主张不能被当作已确认事实。"
                 "用户询问有哪些空白并不是宣称该领域完全空白。不得编造用户断言；没有明确断言时 claims_to_verify=[]。"
                 "claims_to_verify 每项必须逐字摘自当前用户输入，不添加解释、推测或需核查后缀。"
                 "不要把历史助手报告中的假设或结论冒充用户主张。"
                 "research_question 用一句话保留用户要核查的具体实现方向和研究问题，不能改成泛泛的论文综述。"
                 "检索式应分别覆盖最接近的已有实现、同义实现和可能反驳新颖性的工作；"
                 "用户提出相邻领域的新机制时，应保留其来源领域检索，并明确迁移到目标任务时需要核查的差异。"
                 "当前输入可能是对上一轮分析的跟进请求。此时必须利用提供的上一轮结构化方向和会话上下文恢复研究需求，"
                 "不能把‘重新生成报告’、‘按照之前对话’这类操作性句子直接当作检索词。"
                 "如果当前输入明确提出了新研究方向，则以当前输入为准，不要沿用旧方向。"
                 "method_component 是用户关注的方法组成部分或处理环节，使用领域原名，不受固定枚举限制。"
                 "迁移类问题分别检索来源方法及其用于目标任务的已有实现，明确来源和目标，不能反转方向。"
                 "检索式保留目标任务和实现机制，也查同义表述；不要擅自替换成其他任务或固定论文名单。"
             )},
            {"role": "user",
             "content": (
                 f"当前用户输入：{message}\n\n"
                 f"是否为跟进式请求：{'是' if follow_up else '否'}\n"
                 f"上一轮结构化方向：{json.dumps(previous_direction if follow_up else {}, ensure_ascii=False)}\n"
                 f"同一会话最近上下文：\n{(recent_context if follow_up else '') or '无'}\n\n"
                 "请输出如下JSON结构：\n"
                 "{\n"
                 "  \"research_question\": \"要核查的具体研究问题\",\n"
                 "  \"research_domain\": \"研究领域\",\n"
                 "  \"target_task\": \"目标任务或问题\",\n"
                 "  \"method_component\": \"...\",\n"
                 "  \"method_category\": \"...\",\n"
                 "  \"method_subcategory\": \"...\",\n"
                 "  \"implementation_details\": [\"...\"],\n"
                 "  \"claims_to_verify\": [\"待核查事实/新颖性主张\"],\n"
                 "  \"search_queries\": [\"english query 1\", \"english query 2\"],\n"
                 "  \"matrix_axes_hint\": [\"axis 1\", \"axis 2\"]\n"
                 "}\n"
                 "如果某个字段不适用，用空字符串或空数组；不要为了填字段改变用户的研究任务。"
             )},
        ])
        from src.parsing.schemas import DirectionParseResult
        parsed = DirectionParseResult(
            **(result if isinstance(result, dict) else {})
        ).model_dump()
        recovered = False
        if follow_up and previous_direction:
            for key in (
                "research_question", "research_domain", "target_task", "method_component", "method_category", "method_subcategory",
                "implementation_details", "claims_to_verify", "matrix_axes_hint",
            ):
                if not parsed.get(key) and previous_direction.get(key):
                    parsed[key] = previous_direction[key]
                    recovered = True
            queries = parsed.get("search_queries") or []
            if not any(self._query_has_research_focus(q) for q in queries):
                previous_queries = previous_direction.get("search_queries") or []
                if previous_queries:
                    parsed["search_queries"] = previous_queries
                    recovered = True
        parsed['search_queries'] = [q for q in parsed['search_queries'] if self._query_has_research_focus(q)]
        if not parsed.get("search_queries") and self._direction_is_usable(parsed):
            parsed["search_queries"] = [message]
        if not parsed.get("research_question") and self._direction_is_usable(parsed):
            parsed["research_question"] = message
        parsed["user_request"] = message
        if not follow_up:
            parsed['claims_to_verify'] = [claim for claim in parsed.get('claims_to_verify',[]) if claim in message]
        # Mechanism descriptions are research scope, not assertions of novelty.
        if recovered:
            parsed["context_recovered"] = True
        return parsed

    async def _search(self, message: str, direction: dict,
                      config: dict | None = None,
                      selected_paper_ids: list[str] | None = None) -> list[dict]:
        queries = direction.get("search_queries", [message])
        from src.tools.search_tools import search_all, merge_and_deduplicate
        config = config or {}
        search_cfg = config.get("search", {})
        mcp_cfg = config.get("mcp", {})

        selected_paper_ids = list(dict.fromkeys(selected_paper_ids or []))
        selected_id_set = set(selected_paper_ids)

        # User-selected papers are an explicit context, so keep them first.
        all_papers = []
        all_papers.extend(self._selected_papers(selected_paper_ids))

        # Check local KB first
        for q in queries[:3]:
            try:
                local = await self.kb.search_related(q, 10)
                db_papers = []
                for item in local:
                    p = item.get("paper") or item.get("paper_meta", {})
                    if p and p.get("id"):
                        # A previous direction's off-topic label is not a global exclusion.
                        db_papers.append({
                            "id": p.get("id"), "title": p.get("title", ""),
                            "abstract": p.get("abstract", ""),
                            "source": "local_kb", "retrieval_status": p.get("retrieval_status") or "metadata_only",
                            "doi": p.get("doi"), "arxiv_id": p.get("arxiv_id"),
                            "semantic_scholar_id": p.get("semantic_scholar_id"),
                            "url": p.get("url"), "open_access_pdf_url": p.get("open_access_pdf_url"),
                            "fulltext_path": p.get("fulltext_path"),
                            "local_pdf_path": p.get("fulltext_path"),
                            "parsed_markdown_path": p.get("parsed_markdown_path"),
                        })
                all_papers.extend(db_papers)
            except Exception as e:
                logger.debug(f"Local KB search skipped: {e}")

        # The query budget bounds work; an existing paper count does not cover a research question.
        planned_queries = list(dict.fromkeys(queries))[:max(1,min(12,int(search_cfg.get('max_rounds',6))))]
        direction['search_execution'] = {'planned_queries': planned_queries, 'completed_queries': [],
                                         'unexecuted_queries': list(dict.fromkeys(queries))[len(planned_queries):],
                                         'stop_reason': 'query_budget', 'exhaustive': False}
        for i, q in enumerate(planned_queries):
            if i > 0:
                await asyncio.sleep(1)  # rate limit between queries
            results = await search_all(
                q, max(1,min(30,int(search_cfg.get('top_k_per_round',20)))),
                mcp_servers=mcp_cfg.get("servers", []),
                enable_mcp=bool(search_cfg.get("enable_mcp") and mcp_cfg.get("enabled", True)),
                enable_semantic_scholar=bool(search_cfg.get('enable_semantic_scholar',True)),
                enable_arxiv=bool(search_cfg.get('enable_arxiv',True)),
            )
            all_papers.extend(results)
            direction['search_execution']['completed_queries'].append({'query': q, 'returned_records': len(results), 'sources':getattr(results,'diagnostics',[])})
        merged = await merge_and_deduplicate(all_papers)
        result = [self._hydrate_paper_from_db(m.model_dump()) for m in merged]
        new_limit=max(0,min(100,int(config.get('research',{}).get('max_new_papers',30))))
        retained=[p for p in result if p.get('id') in selected_id_set]
        additions=[p for p in result if p.get('id') not in selected_id_set]
        result=retained+additions[:new_limit]
        direction['search_execution']['new_papers']=len(additions[:new_limit])
        direction['search_execution']['deferred_new_records']=max(0,len(additions)-new_limit)
        # Relevance scoring with LLM
        if len(result) > 10:
            try:
                snippet = json.dumps([
                    {"id": p.get("id"), "title": p.get("title"),
                     "abstract": (p.get("abstract") or "")[:200]}
                    for p in result
                ], ensure_ascii=False)
                ranking = await self.llm.chat_json([
                    {"role": "system",
                     "content": "你是研究空白核查助手。按当前研究问题对论文相关性打分。只输出JSON。"},
                    {"role": "user",
                     "content": f"方向：{json.dumps(direction)}\n论文：{snippet}\n输出 ranked_papers: [{{paper_id, relevance_score, reason}}]"},
                ])
                ranking = self._normalize_json_object(ranking, "ranked_papers")
                ranked = ranking.get("ranked_papers", [])
                if ranked:
                    score_map = {}
                    for r in ranked:
                        pid = r.get("paper_id") or r.get("id") or ""
                        if pid:
                            score_map[pid] = r.get("relevance_score", 0)
                    if score_map:
                        result.sort(key=lambda p: score_map.get(p.get("id", ""), 0), reverse=True)
            except Exception as e:
                logger.warning(f"Relevance scoring failed: {e}")
        if selected_id_set:
            selected_order = {pid: i for i, pid in enumerate(selected_paper_ids)}
            result.sort(key=lambda p: selected_order.get(p.get("id", ""), len(selected_order)))
        return result

    def _parse_budget(self, papers: list[dict], configured_top_k: int, config: dict | None = None) -> int:
        try:
            configured_top_k = int(configured_top_k)
        except Exception:
            configured_top_k = 50
        # The visible setting is authoritative. Old hidden v0 caps must not override it.
        return max(0, min(len(papers), configured_top_k, 100))

    def _has_public_fulltext_link(self, paper: dict) -> bool:
        url = str(paper.get("open_access_pdf_url") or paper.get("pdf_url") or paper.get("url") or "").lower()
        if not url and paper.get("arxiv_id"):
            return True
        blocked = ("ieeexplore.ieee.org", "sciencedirect.com", "elsevier.com", "dl.acm.org")
        if any(host in url for host in blocked):
            return False
        return "arxiv.org/abs/" in url or "arxiv.org/pdf/" in url or url.endswith(".pdf") or "/content/pdf/" in url


    def _selected_papers(self, paper_ids: list[str]) -> list[dict]:
        papers = []
        for pid in paper_ids:
            row = self.db.fetchone("SELECT * FROM papers WHERE id=?", (pid,)) if self.db else None
            if not row:
                continue
            try:
                authors = json.loads(row.get("authors_json") or "[]")
            except Exception:
                authors = []
            papers.append({
                "id": row.get("id"),
                "title": row.get("title", ""),
                "authors": authors if isinstance(authors, list) else [],
                "year": row.get("year"),
                "venue": row.get("venue"),
                "doi": row.get("doi"),
                "arxiv_id": row.get("arxiv_id"),
                "semantic_scholar_id": row.get("semantic_scholar_id"),
                "url": row.get("url"),
                "open_access_pdf_url": row.get("open_access_pdf_url"),
                "abstract": row.get("abstract", ""),
                "citation_count": row.get("citation_count") or 0,
                "source": "selected_library",
                "is_user_selected": True,
                "retrieval_status": row.get("retrieval_status") or "metadata_only",
                "local_pdf_path": row.get("fulltext_path"),
            })
        return papers

    def _hydrate_paper_from_db(self, paper: dict) -> dict:
        if not self.db or not paper:
            return paper
        clauses = []
        params = []
        for col, value in (
            ("id", paper.get("id")),
            ("doi", paper.get("doi")),
            ("arxiv_id", paper.get("arxiv_id")),
            ("semantic_scholar_id", paper.get("semantic_scholar_id")),
        ):
            if value:
                clauses.append(f"{col}=?")
                params.append(value)
        title = " ".join(str(paper.get("title") or "").lower().split())
        if title:
            clauses.append("lower(title)=?")
            params.append(title)
        if not clauses:
            return paper
        row = self.db.fetchone(f"SELECT * FROM papers WHERE {' OR '.join(clauses)} LIMIT 1", tuple(params))
        if not row:
            return paper
        hydrated = dict(paper)
        hydrated["id"] = row.get("id") or hydrated.get("id")
        for key in ("title", "abstract", "venue", "doi", "arxiv_id", "semantic_scholar_id", "url", "open_access_pdf_url"):
            if not hydrated.get(key) and row.get(key):
                hydrated[key] = row.get(key)
        if row.get("year") and not hydrated.get("year"):
            hydrated["year"] = row.get("year")
        if row.get("citation_count") and not hydrated.get("citation_count"):
            hydrated["citation_count"] = row.get("citation_count")
        status = row.get("retrieval_status") or hydrated.get("retrieval_status") or "metadata_only"
        if status in ("downloaded", "parsed", "off_topic"):
            hydrated["retrieval_status"] = status
            hydrated["missing_reason"] = row.get("missing_reason")
        if row.get("fulltext_path"):
            hydrated["fulltext_path"] = row.get("fulltext_path")
            hydrated["local_pdf_path"] = row.get("fulltext_path")
        if row.get("parsed_markdown_path"):
            hydrated["parsed_markdown_path"] = row.get("parsed_markdown_path")
        return hydrated

    def _paper_id(self, paper) -> str:
        if isinstance(paper, dict):
            return str(paper.get("id") or "")
        return str(getattr(paper, "id", "") or "")






    @staticmethod
    def _enrich_profile_metadata(profile: dict, paper: dict) -> dict:
        from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer

        enriched = dict(profile)
        enriched["paper_id"] = str(
            enriched.get("paper_id") or paper.get("id") or ""
        )
        enriched["paper_title"] = str(paper.get("title") or "")
        enriched["venue"] = str(paper.get("venue") or "")
        enriched["retrieval_status"] = str(paper.get("retrieval_status") or "")
        enriched["source_type"] = EvidenceGroundedAnalyzer._source_type({
            **paper,
            **enriched,
            "title": paper.get("title"),
        })
        return enriched


    async def _extract_innovations(self, ctx, parse_result,
                                   papers: list[dict] | None = None) -> list[dict]:
        from src.parsing.innovation_extractor import InnovationExtractor
        extractor = InnovationExtractor(self.llm)
        profiles = []

        for pid in parse_result.data.get("parsed", []):
            import re
            parsed_dir = ctx.workspace_root / "papers" / "parsed" / re.sub(r'[^A-Za-z0-9._-]+','_',pid)
            md_path = parsed_dir / "full.md"
            stored_paper = self.db.fetchone('SELECT * FROM papers WHERE id=?', (pid,)) or {}
            stored_path = stored_paper.get('parsed_markdown_path')
            if stored_path:
                candidate = Path(stored_path)
                if not candidate.is_absolute(): candidate = ctx.workspace_root / candidate
                candidate = candidate.resolve()
                if candidate.is_relative_to(ctx.workspace_root.resolve()) and candidate.is_file():
                    md_path = candidate
            if not md_path.exists():
                candidates = list(parsed_dir.rglob("*.md")) if parsed_dir.exists() else []
                if candidates:
                    md_path = candidates[0]
            if md_path.exists():
                paper = self.db.fetchone("SELECT * FROM papers WHERE id=?", (pid,))
                if paper:
                    cached = self._verified_cached_profile(pid, md_path.read_text(encoding='utf-8'))
                    if cached:
                        profiles.append(self._enrich_profile_metadata(cached, paper))
                        await self._progress(ctx.session_id,'InnovationExtractor','paper_cached',f"已核对原文，复用证据：{paper.get('title') or pid}")
                        continue
                    await self._progress(ctx.session_id,'InnovationExtractor','paper_extract_started',f"正在提取：{paper.get('title') or pid}",{'paper_id':pid,'source':'fulltext'})
                    p = self._normalize_profile(await extractor.extract_from_markdown(pid, dict(paper), md_path), pid)
                    p["has_fulltext"] = True
                    p["paper_id"] = pid
                    p = self._enrich_profile_metadata(p, paper)
                    profiles.append(p)
                    await self._progress(ctx.session_id,'InnovationExtractor','paper_extract_completed',f"{'提取失败' if p.get('error') else '提取完成'}：{paper.get('title') or pid}",{'paper_id':pid,'error':p.get('error'),'evidence_count':len(p.get('evidence',[]))})

        for pid in parse_result.data.get("metadata_only", []):
            paper = self.db.fetchone("SELECT * FROM papers WHERE id=?", (pid,))
            if paper:
                cached = self._verified_cached_profile(pid, paper.get('abstract') or '')
                if cached:
                    profiles.append(self._enrich_profile_metadata(cached, paper))
                    continue
                await self._progress(ctx.session_id,'InnovationExtractor','paper_extract_started',f"正在提取摘要：{paper.get('title') or pid}",{'paper_id':pid,'source':'abstract'})
                p = self._normalize_profile(await extractor.extract_from_abstract(pid, dict(paper)), pid)
                p["has_fulltext"] = False
                p["paper_id"] = pid
                p = self._enrich_profile_metadata(p, paper)
                profiles.append(p)
                await self._progress(ctx.session_id,'InnovationExtractor','paper_extract_completed',f"{'提取失败' if p.get('error') else '摘要提取完成'}：{paper.get('title') or pid}",{'paper_id':pid,'error':p.get('error'),'evidence_count':len(p.get('evidence',[]))})

        # The parse budget can be smaller than the current evidence set. Reuse
        # previously extracted profiles for papers that are in this run rather
        # than silently dropping their counter-evidence from coverage mapping.
        seen_ids = {str(item.get("paper_id") or "") for item in profiles}
        for paper in papers or []:
            pid = str(paper.get("id") or "")
            if not pid or pid in seen_ids:
                continue
            row = self.db.fetchone(
                "SELECT profile_json FROM innovation_profiles WHERE paper_id=?",
                (pid,),
            ) if self.db else None
            if not row:
                continue
            try:
                saved = json.loads(row.get("profile_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            saved = self._normalize_profile(saved, pid)
            saved["has_fulltext"] = bool(
                saved.get("has_fulltext")
                or paper.get("fulltext_path") or paper.get("local_pdf_path")
                or paper.get("parsed_markdown_path")
            )
            saved = self._enrich_profile_metadata(saved, paper)
            profiles.append(saved)
            seen_ids.add(pid)

        from src.analysis.provenance import bind_profile
        verified=[]
        for profile in profiles:
            paper=self.db.fetchone('SELECT * FROM papers WHERE id=?',(profile.get('paper_id'),)) or {}
            value=paper.get('parsed_markdown_path')
            source='abstract'
            text=paper.get('abstract') or ''
            fulltext=False
            if value:
                path=Path(value)
                if not path.is_absolute(): path=ctx.workspace_root/path
                path=path.resolve()
                if path.is_relative_to(ctx.workspace_root.resolve()) and path.is_file():
                    text=path.read_text(encoding='utf-8')
                    source=str(path.relative_to(ctx.workspace_root.resolve())).replace('\\','/')
                    fulltext=True
            from src.parsing.content_quality import unusable_fulltext_reason
            if fulltext and unusable_fulltext_reason(text):
                profile['error']=unusable_fulltext_reason(text)
                profile['evidence']=[]
                fulltext=False
                text=paper.get('abstract') or ''
                source='abstract'
            profile['has_fulltext']=fulltext
            verified.append(bind_profile(profile,text,source))
        return verified

    def _verified_cached_profile(self, pid, text):
        import hashlib
        row = self.db.fetchone('SELECT profile_json FROM innovation_profiles WHERE paper_id=?',(pid,))
        if not row:return None
        try:profile=json.loads(row.get('profile_json') or '{}')
        except (TypeError,ValueError):return None
        if profile.get('error') or not profile.get('evidence_verified') or not profile.get('evidence'):
            return None
        if profile.get('document_sha256')!=hashlib.sha256(text.encode('utf-8')).hexdigest():
            return None
        return self._normalize_profile(profile,pid)

    async def _analyze_gaps(self, direction, innovations):
        from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
        return await EvidenceGroundedAnalyzer(self.llm).analyze(
            direction, innovations
        )

    def _normalize_json_object(self, value, list_key: str | None = None) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {list_key or "items": value}
        if value is None:
            return {}
        return {list_key or "value": value}

    def _normalize_analysis(self, value) -> dict:
        data = self._normalize_json_object(value, "gaps")
        gap_keys = (
            "gaps", "candidate_gaps", "gap_analysis", "innovation_gaps",
            "research_gaps", "potential_gaps", "空白", "空白点", "候选空白",
            "候选创新空白", "创新空白", "研究空白",
        )
        gaps = None
        explicit_gap_field = False
        for key in gap_keys:
            if key in data:
                explicit_gap_field = True
                candidate = data.get(key)
                if candidate not in (None, [], {}):
                    gaps = candidate
                    break
        if isinstance(gaps, dict):
            for key in ("items", "list", "gaps", "候选空白", "空白点"):
                if isinstance(gaps.get(key), list):
                    gaps = gaps.get(key)
                    break
        if gaps is None and not explicit_gap_field:
            gaps = self._find_gap_like_list(data)
        if not isinstance(gaps, list):
            gaps = []
        data["gaps"] = [self._normalize_gap(g) for g in gaps if isinstance(g, dict)]
        matrix = None
        for key in (
            "matrix", "coverage_matrix", "innovation_matrix",
            "创新矩阵", "覆盖矩阵", "创新覆盖矩阵",
        ):
            candidate = data.get(key)
            if isinstance(candidate, dict):
                matrix = candidate
                if candidate:
                    break
        data["matrix"] = matrix or {}
        for target, keys in (
            ("provisional_gaps", ("provisional_gaps", "unverified_gaps", "待验证候选")),
            ("rejected_gaps", ("rejected_gaps", "rejected_candidates", "contradicted_gaps", "已否决候选")),
        ):
            items = []
            for key in keys:
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
            data[target] = [
                self._normalize_gap(item) for item in items if isinstance(item, dict)
            ]
            if target=='rejected_gaps':
                for item in data[target]: item['candidate_status']='rejected'
        return data

    def _matrix_coverage_stats(self, matrix: dict,
                               paper_ids: set[str] | None = None) -> dict:
        stats = {
            "structural_valid": False,
            "evidence_assigned": False,
            "assigned_paper_ids": [],
            "unknown_paper_ids": [],
            "cell_count": 0,
        }
        if not isinstance(matrix, dict) or not matrix:
            return stats
        axes = (
            matrix.get("axes") or matrix.get("dimensions")
            or matrix.get("matrix_axes") or matrix.get("分析轴") or []
        )
        cells = (
            matrix.get("cells") or matrix.get("coverage")
            or matrix.get("entries") or matrix.get("单元格") or []
        )
        if isinstance(axes, dict):
            axes = list(axes)
        if isinstance(cells, dict):
            cells = list(cells.values())
        rows = matrix.get("rows") or matrix.get("行") or []
        columns = matrix.get("columns") or matrix.get("cols") or matrix.get("列") or []
        stats["structural_valid"] = bool(
            isinstance(cells, list) and cells and (
                isinstance(axes, list) and len(axes) >= 2
                or rows and columns
            )
        )
        if not stats["structural_valid"]:
            return stats
        stats["cell_count"] = len(cells)
        assigned = set()
        unknown = set()
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            coverage = str(cell.get("coverage") or "").strip().lower()
            if coverage in {
                "empty", "unclassified", "none", "unknown", "uncertain",
                "空白", "未覆盖", "未分类", "无", "不确定",
            }:
                continue
            cell_paper_ids = cell.get("paper_ids") or []
            if not isinstance(cell_paper_ids, list):
                cell_paper_ids = [cell_paper_ids]
            for paper_id in cell_paper_ids:
                paper_id = str(paper_id)
                if paper_ids is None or paper_id in paper_ids:
                    assigned.add(paper_id)
                else:
                    unknown.add(paper_id)
        stats["assigned_paper_ids"] = sorted(assigned)
        stats["unknown_paper_ids"] = sorted(unknown)
        stats["evidence_assigned"] = bool(assigned)
        return stats

    def _matrix_has_coverage(self, matrix: dict,
                             paper_ids: set[str] | None = None) -> bool:
        stats = self._matrix_coverage_stats(matrix, paper_ids)
        return stats["structural_valid"] and stats["evidence_assigned"]

    def _enforce_analysis_reliability(self, direction: dict,
                                      innovations: list[dict],
                                      analysis: dict) -> dict:
        data = self._normalize_analysis(analysis)
        gaps = data.get("gaps", [])
        matrix = data.get("matrix", {})
        relevant_innovations = [
            item for item in innovations
            if not item.get("error")
            and (
                not item.get("source_type")
                or item.get("source_type") in {"paper_fulltext", "paper_abstract"}
            )
        ]
        fulltext_count = sum(
            1 for item in relevant_innovations
            if item.get("source_type") == "paper_fulltext"
            or not item.get("source_type") and item.get("has_fulltext")
        )
        paper_ids = {
            str(item.get("paper_id")) for item in relevant_innovations
            if item.get("paper_id")
        }
        matrix_stats = self._matrix_coverage_stats(matrix, paper_ids)
        coverage_records_present = "coverage_records" in data
        coverage_records = [
            item for item in (data.get("coverage_records") or [])
            if isinstance(item, dict)
        ]
        record_ids = {
            str(item.get("paper_id")) for item in coverage_records
            if item.get("coverage_eligible")
            and item.get("coverage_basis") == "research_claims"
            and item.get("evidence_ids")
            and item.get("coverage_level") in {"direct", "partial"}
            and item.get("paper_id") in paper_ids
        }
        matrix_record_consistent = (
            not coverage_records_present
            or bool(record_ids)
            and set(matrix_stats["assigned_paper_ids"]).issubset(record_ids)
        )
        audit = data.get("coverage_audit") or []
        audited_ids = {
            str(item.get("paper_id")) for item in audit
            if isinstance(item, dict)
            and item.get("assessment_status") == "audited"
            and item.get("paper_id") in paper_ids
        }
        fallback_ids = {
            str(item.get("paper_id")) for item in audit
            if isinstance(item, dict)
            and item.get("audit_mode") == "deterministic_fallback"
            and item.get("paper_id") in paper_ids
        }
        unassessed_ids = sorted(paper_ids - audited_ids) if audit else []
        reliability = {
            "status": "ok",
            "matrix_valid": bool(
                matrix_stats["structural_valid"]
                and matrix_stats["evidence_assigned"]
                and matrix_record_consistent
            ),
            "matrix_structural_valid": matrix_stats["structural_valid"],
            "matrix_assigned_papers": len(matrix_stats["assigned_paper_ids"]),
            "coverage_records": len(coverage_records),
            "matrix_record_consistent": matrix_record_consistent,
            "innovation_profiles": len(relevant_innovations),
            "fulltext_profiles": fulltext_count,
            "audited_profiles": len(audited_ids) if audit else None,
            "deterministic_fallback_profiles": len(fallback_ids),
            "unassessed_paper_ids": unassessed_ids,
            "blocked_gap_count": 0,
            "rejected_gap_count": len(data.get("rejected_gaps", [])),
            "warnings": [],
        }

        if not reliability["matrix_valid"]:
            if gaps:
                for gap in gaps:
                    gap["candidate_status"] = "insufficient_evidence"
                data["provisional_gaps"] = data.get("provisional_gaps", []) + gaps
            reliability["blocked_gap_count"] = len(data.get("provisional_gaps", []))
            data["gaps"] = []
            reliability["status"] = "blocked"
            if matrix_stats["structural_valid"]:
                reliability["warnings"].append(
                    "创新矩阵虽有轴和单元格，但没有把任何本轮真实论文归入 covered/partial 单元。"
                    "全空矩阵只能说明自动分类失败或本轮未找到，不能证明文献不存在；候选已降为未验证草案。"
                )
                if coverage_records_present and not matrix_record_consistent:
                    reliability["warnings"].append(
                        "矩阵归类与逐篇 coverage records 不一致；模型自报矩阵不能进入候选生成。"
                    )
            else:
                reliability["warnings"].append(
                    "创新覆盖矩阵为空或缺少分析轴/覆盖单元，本轮模型提出的候选已降为未验证草案，不进入正式结论。"
                )
            data["warning"] = reliability["warnings"][0]
            data["reliability"] = reliability
            return data

        if audit and unassessed_ids:
            if gaps:
                for gap in gaps:
                    gap["candidate_status"] = "insufficient_evidence"
                data["provisional_gaps"] = data.get("provisional_gaps", []) + gaps
            reliability["blocked_gap_count"] = len(data.get("provisional_gaps", []))
            data["gaps"] = []
            reliability["status"] = "blocked"
            reliability["warnings"].append(
                f"有 {len(unassessed_ids)} 篇创新画像未完成逐篇证据审计；它们可能包含反证，"
                "因此本轮候选全部降为未验证草案。"
            )
            data["reliability"] = reliability
            return data

        if fallback_ids:
            if gaps:
                for gap in gaps:
                    gap["candidate_status"] = "insufficient_evidence"
                data["provisional_gaps"] = data.get("provisional_gaps", []) + gaps
            reliability["blocked_gap_count"] = len(data.get("provisional_gaps", []))
            data["gaps"] = []
            reliability["status"] = "blocked" if gaps else "degraded"
            reliability["warnings"].append(
                f"有 {len(fallback_ids)} 篇论文仅完成本地确定性字段映射，"
                "未完成 LLM 语义反证；coverage matrix 可用，但候选新颖性全部暂缓。"
            )
            data["reliability"] = reliability
            if gaps:
                return data

        if fulltext_count == 0 and gaps:
            reliability["status"] = "degraded"
            reliability["warnings"].append(
                "本轮创新画像全部来自摘要或元数据，候选置信度最高限制为 0.55。"
            )

        accepted_gaps = []
        provisional_gaps = list(data.get("provisional_gaps", []))
        rejected_gaps = list(data.get("rejected_gaps", []))
        for gap in gaps:
            notes = []
            caps = []
            status = str(gap.get("candidate_status") or "insufficient_evidence").lower()
            if status in {"contradicted", "rejected", "reject"}:
                gap["reliability_notes"] = ["候选已被覆盖证据否定，不进入正式结论。"]
                rejected_gaps.append(gap)
                continue
            if status not in {"supported_candidate", "narrow_candidate"}:
                gap["candidate_status"] = "insufficient_evidence"
                gap["reliability_notes"] = ["候选状态不是证据支持的窄义候选，保留为待验证草案。"]
                provisional_gaps.append(gap)
                continue
            nearest = gap.get("nearest_works") or []
            valid_nearest = [
                item for item in nearest
                if isinstance(item, dict)
                and str(item.get("paper_id") or "") in paper_ids
                and str(item.get("difference") or item.get("description") or "").strip()
            ]
            if not valid_nearest:
                gap["candidate_status"] = "insufficient_evidence"
                gap["reliability_notes"] = [
                    "没有引用本轮证据库中的真实最近工作并说明直接差异，不能作为正式空白。"
                ]
                provisional_gaps.append(gap)
                continue
            if not str(gap.get("novelty_basis") or "").strip():
                gap["candidate_status"] = "insufficient_evidence"
                gap["reliability_notes"] = [
                    "缺少相对最近工作的任务特定新颖性依据，不能作为正式空白。"
                ]
                provisional_gaps.append(gap)
                continue
            experiment = gap.get("decisive_experiment")
            experiment_complete = (
                isinstance(experiment, dict)
                and bool(experiment.get("nearest_method_baselines"))
                and bool(str(experiment.get("minimal_change") or "").strip())
                and bool(experiment.get("supporting_metrics"))
                and bool(experiment.get("stop_conditions"))
            )
            if not experiment_complete:
                gap["candidate_status"] = "insufficient_evidence"
                gap["reliability_notes"] = [
                    "缺少候选特定的最近方法基线、最小变量、支持指标或停止条件，不能作为正式空白。"
                ]
                provisional_gaps.append(gap)
                continue
            cited_ids = {str(item.get("paper_id")) for item in valid_nearest}
            cited_fulltext = any(
                str(item.get("paper_id")) in cited_ids and item.get("has_fulltext")
                for item in relevant_innovations
            )
            if not cited_fulltext:
                caps.append(0.55)
                notes.append("最近工作没有全文直接证据，置信度上限为 0.55。")
            if fulltext_count == 0:
                caps.append(0.55)
            if status == "narrow_candidate":
                caps.append(0.65)
            try:
                confidence = float(gap.get("confidence"))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            if caps:
                confidence = min(confidence, *caps)
            gap["confidence"] = round(confidence, 2)
            if notes:
                gap["reliability_notes"] = notes
                reliability["status"] = "degraded"
            accepted_gaps.append(gap)

        data["gaps"] = accepted_gaps
        data["provisional_gaps"] = provisional_gaps
        data["rejected_gaps"] = rejected_gaps
        reliability["blocked_gap_count"] = len(provisional_gaps)
        reliability["rejected_gap_count"] = len(rejected_gaps)
        if provisional_gaps:
            reliability["status"] = "degraded" if accepted_gaps else "blocked"
            reliability["warnings"].append(
                "部分候选因状态、最近工作或新颖性证据不满足要求，已移入未验证草案。"
            )
        data["reliability"] = reliability
        return data

    def _apply_critic_verdicts(self, analysis: dict, critic: dict,
                               innovations: list[dict]) -> dict:
        data = self._normalize_analysis(analysis)
        reviews = critic.get("gap_reviews", []) if isinstance(critic, dict) else []
        by_id = {}
        by_name = {}
        for review in reviews:
            if not isinstance(review, dict):
                continue
            if review.get("gap_id"):
                by_id[str(review["gap_id"])] = review
            if review.get("gap_name"):
                by_name[str(review["gap_name"])] = review

        accepted = []
        provisional = list(data.get("provisional_gaps", []))
        rejected = list(data.get("rejected_gaps", []))
        for gap in data.get("gaps", []):
            review = by_id.get(str(gap.get("gap_id") or "")) or by_name.get(str(gap.get("name") or ""))
            if not review:
                gap["critic_verdict"] = "provisional"
                notes = gap.get("reliability_notes") or []
                if not isinstance(notes, list):
                    notes = [str(notes)]
                notes.append(
                    "Critic 没有返回该候选的逐项裁决，已按失败安全原则降为草案。"
                )
                gap["reliability_notes"] = notes
                provisional.append(gap)
                continue
            verdict = str(review.get("verdict") or "provisional").lower()
            gap["critic_verdict"] = verdict
            gap["critic_reason"] = str(review.get("reason") or "")
            gap["critic_contradicted_by"] = [
                str(pid) for pid in (review.get("contradicted_by") or [])
            ]
            try:
                cap = max(0.0, min(1.0, float(review.get("confidence_cap"))))
                gap["confidence"] = round(min(float(gap.get("confidence") or 0), cap), 2)
            except (TypeError, ValueError):
                pass
            if verdict == "reject":
                if gap.get("contradicted_by"):
                    gap["candidate_status"] = "contradicted"
                else:
                    gap["candidate_status"] = "insufficient_evidence"
                rejected.append(gap)
            elif verdict == "provisional":
                gap["candidate_status"] = "insufficient_evidence"
                provisional.append(gap)
            elif verdict == "revise":
                revised_description = str(review.get("revised_description") or "").strip()
                if not revised_description:
                    notes = gap.get("reliability_notes") or []
                    if not isinstance(notes, list):
                        notes = [str(notes)]
                    notes.append(
                        "Critic 要求收窄但未给出可执行改写，已降为草案。"
                    )
                    gap["reliability_notes"] = notes
                    gap["candidate_status"] = "insufficient_evidence"
                    provisional.append(gap)
                    continue
                if review.get("revised_name"):
                    gap["name"] = str(review["revised_name"])
                gap["description"] = revised_description
                gap["candidate_status"] = "insufficient_evidence"
                gap["revision_requires_verification"] = True
                provisional.append(gap)
            elif verdict == "accept":
                accepted.append(gap)
            else:
                gap["candidate_status"] = "insufficient_evidence"
                provisional.append(gap)

        data["gaps"] = accepted
        data["provisional_gaps"] = provisional
        data["rejected_gaps"] = rejected
        data["critic"] = critic
        from src.analysis.direction_coverage import enforce_candidate_consistency
        data = enforce_candidate_consistency(data)
        accepted = data.get("gaps") or []
        provisional = data.get("provisional_gaps") or []
        rejected = data.get("rejected_gaps") or []
        reliability = data.setdefault("reliability", {})
        reliability["blocked_gap_count"] = len(provisional)
        reliability["rejected_gap_count"] = len(rejected)
        if not accepted and (provisional or rejected):
            reliability["status"] = "blocked"
        elif provisional or rejected:
            reliability["status"] = "degraded"
        if provisional or rejected:
            warnings = reliability.setdefault("warnings", [])
            warning = '本轮没有正式候选，未调用 Critic 模型复核；下列为证据审计后的待验证或未采纳项。' if critic.get('skipped') else (
                f"Critic 逐项裁决后保留 {len(accepted)} 个正式候选，"
                f"暂缓 {len(provisional)} 个，否决 {len(rejected)} 个。"
            )
            if warning not in warnings:
                warnings.append(warning)
        return data

    def _find_gap_like_list(self, value) -> list | None:
        if isinstance(value, dict):
            for item in value.values():
                found = self._find_gap_like_list(item)
                if found is not None:
                    return found
        if isinstance(value, list) and value and all(isinstance(i, dict) for i in value):
            gap_markers = {"name", "description", "nearest_works", "feasibility", "novelty", "名称", "描述", "核心想法", "最接近工作", "可行性", "新颖性"}
            if any(gap_markers.intersection(i.keys()) for i in value):
                return value
        return None

    def _normalize_gap(self, gap: dict) -> dict:
        def first(*keys):
            for key in keys:
                value = gap.get(key)
                if value not in (None, ""):
                    return value
            return None

        normalized = dict(gap)
        normalized["gap_id"] = first("gap_id", "id", "candidate_id", "候选ID") or ""
        normalized["candidate_status"] = first(
            "candidate_status", "status", "verdict", "候选状态"
        ) or "insufficient_evidence"
        normalized["name"] = first("name", "title", "gap_name", "名称", "空白名称", "候选空白", "创新空白") or "未命名空白"
        normalized["description"] = first("description", "core_idea", "idea", "summary", "描述", "核心想法", "说明") or ""
        normalized["feasibility"] = first("feasibility", "可行性")
        normalized["novelty"] = first("novelty", "新颖性")
        normalized["difficulty"] = first("difficulty", "难度")
        normalized["confidence"] = first("confidence", "置信度")
        normalized["nearest_works"] = first("nearest_works", "similar_works", "closest_works", "related_works", "最接近工作", "近似工作") or []
        normalized["evidence"] = first("evidence", "证据") or []
        normalized["why_original_may_be_weak"] = first(
            "why_original_may_be_weak", "original_weakness", "why_not_satisfying",
            "为什么原报告可能不够好", "原报告不足", "不满意原因",
        ) or ""
        normalized["coverage_risk"] = first(
            "coverage_risk", "already_solved_risk", "risk_of_being_already_solved",
            "可能已被覆盖的风险", "已覆盖风险", "反证风险",
        ) or ""
        normalized["required_evidence"] = first(
            "required_evidence", "missing_evidence", "must_read", "needed_evidence",
            "必须补读证据", "需要补读", "需要补充证据",
        ) or []
        normalized["decisive_experiment"] = first(
            "decisive_experiment", "minimal_decisive_experiment", "minimum_experiment",
            "最低判别实验", "最小验证实验", "判别实验",
        ) or ""
        normalized["alternative_framing"] = first(
            "alternative_framing", "alternative_statement", "backup_direction",
            "备选研究表述", "备选角度", "改写方向",
        ) or ""
        normalized["novelty_basis"] = first(
            "novelty_basis", "evidence_for_novelty", "新颖性依据", "窄义差异"
        ) or ""
        normalized["task_specific_contribution"] = first(
            "task_specific_contribution", "任务特定贡献",
        ) or ""
        return normalized

    def _normalize_profile(self, value, paper_id: str) -> dict:
        if isinstance(value, list):
            value = value[0] if value and isinstance(value[0], dict) else {"innovation_detail": str(value)}
        if not isinstance(value, dict):
            value = {"innovation_detail": str(value), "confidence": 0.0}
        value.setdefault("paper_id", paper_id)
        # Read compatibility only; new tasks never emit or branch on the legacy field.
        legacy_component = value.pop('stereo_matching_stage', None)
        if legacy_component and not value.get('method_component'):
            value['method_component'] = legacy_component
        evidence = value.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [evidence]
        value["evidence"] = evidence
        return value

    async def _generate_report(self, ctx, direction, papers, innovations,
                               analysis, missing,
                               selected_paper_ids: list[str] | None = None,
                               title_hint: str | None = None):
        ws_root = ctx.workspace_root
        reports_dir = ws_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        title_parts = []
        slug_parts = []
        for key in ("target_task", "research_domain", "method_category",
                    "method_subcategory", "method_component"):
            value = (direction or {}).get(key)
            if value:
                value = str(value).strip().replace("\n", " ")
                if value and value not in title_parts:
                    title_parts.append(value)
                slug = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
                if slug and slug not in slug_parts:
                    slug_parts.append(slug)
        if not title_parts and title_hint:
            hint = str(title_hint).strip().replace("\n", " ")
            if hint:
                title_parts.append(hint[:48])
        report_title = str(direction.get('user_request') or direction.get('research_question') or title_hint or '研究空白分析').strip()[:100]
        report_slug = "_".join(slug_parts[:3])[:60] if slug_parts else ""
        report_id = uuid.uuid4().hex[:12]
        filename = f"gap_analysis_{ts}_{report_id}_{report_slug}.md" if report_slug else f"gap_analysis_{ts}_{report_id}.md"
        report_path = reports_dir / filename

        if analysis.get('reader_report'):
            # Research records stay available separately; no internal JSON in the reader report.
            audit_dir = ws_root / '.agent_history' / 'research'
            audit_dir.mkdir(parents=True, exist_ok=True)
            audit = dict(analysis)
            audit.pop('reader_report', None)
            with (audit_dir / (report_path.stem + '.json')).open('x', encoding='utf-8') as output:
                json.dump({'direction':direction,'analysis':audit}, output, ensure_ascii=False, indent=2)
            with report_path.open('x', encoding='utf-8') as output:
                output.write(analysis['reader_report'])
            return report_path

        def clean(value, limit: int | None = None):
            from src.analysis.report_text import readable_text
            text = readable_text(value).replace("\n", " ").strip()
            text = " ".join(text.split())
            if limit and len(text) > limit:
                text = text[:limit].rstrip() + "..."
            return text

        def cell(value, limit: int | None = None):
            return clean(value, limit).replace("|", "\\|") or "-"

        def paper_title(pid: str):
            row = self.db.fetchone("SELECT title FROM papers WHERE id=?", (pid,)) if self.db else None
            return row.get("title") if row else pid

        def paper_source_type(p: dict):
            from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
            profile = next((
                item for item in innovations
                if str(item.get("paper_id") or "") == str(p.get("id") or "")
            ), {})
            return EvidenceGroundedAnalyzer._source_type({
                **p, **profile, "title": p.get("title"),
            })

        def paper_line(p: dict):
            title = clean(p.get("title") or p.get("id"), 120)
            venue = " / ".join(str(x) for x in (p.get("venue"), p.get("year")) if x) or "未知来源"
            status = p.get("retrieval_status") or "metadata_only"
            source = p.get("source") or "library"
            source_type = paper_source_type(p)
            return f"- **{title}**（{venue}，{source_type}，{source}，{status}）"

        def evidence_text(ev):
            if isinstance(ev, dict):
                text = ev.get("quote") or ev.get("supports") or ev.get("difference") or ev.get("description")
                if text:
                    pid = ev.get("paper_id")
                    prefix = f"{paper_title(pid)}：" if pid else ""
                    return clean(prefix + str(text), 260)
                return clean(json.dumps(ev, ensure_ascii=False), 260)
            return clean(ev, 260)

        def count_by(items, key):
            counts = {}
            for item in items:
                value = item.get(key) or "unknown"
                counts[value] = counts.get(value, 0) + 1
            return counts

        def listify(value):
            if not value:
                return []
            if isinstance(value, list):
                return value
            return [value]

        def experiment_lines(value):
            if not isinstance(value, dict):
                return ["  - 实验结构缺失；该候选不能进入正式结论。"]
            baselines = listify(value.get("nearest_method_baselines"))
            metrics = listify(value.get("supporting_metrics"))
            stops = listify(value.get("stop_conditions"))
            lines = []
            if baselines:
                lines.append("  - 最近方法基线：" + "；".join(clean(item, 100) for item in baselines))
            lines.append("  - 单一变量：" + clean(value.get("minimal_change"), 400))
            if metrics:
                lines.append("  - 支持指标：" + "；".join(clean(item, 120) for item in metrics))
            if stops:
                lines.append("  - 停止条件：" + "；".join(clean(item, 160) for item in stops))
            return lines

        gaps = analysis.get("gaps", []) if isinstance(analysis, dict) else []
        selected_id_set = set(selected_paper_ids or [])
        selected_count = len(selected_id_set)
        selected_papers = [
            p for p in papers if str(p.get("id") or "") in selected_id_set
        ]
        source_typed_papers = [(p, paper_source_type(p)) for p in papers]
        fulltext_papers = [
            p for p, kind in source_typed_papers if kind == "paper_fulltext"
        ]
        abstract_papers = [
            p for p, kind in source_typed_papers if kind == "paper_abstract"
        ]
        nonpaper_sources = [
            p for p, kind in source_typed_papers
            if kind in {
                "survey_or_repository", "prior_knowledge",
                "model_generated_summary",
            }
        ]
        fulltext_count = sum(
            1 for item in innovations
            if item.get("source_type") == "paper_fulltext"
        )
        abstract_count = sum(
            1 for item in innovations
            if item.get("source_type") == "paper_abstract"
        )
        source_counts = count_by(papers, "source")
        retrieved_selected_count = source_counts.get("selected_library", 0)
        counterevidence_count = source_counts.get("local_counterevidence", 0)
        local_kb_count = source_counts.get("local_kb", 0)
        total_unique_records = len({
            str(p.get("id") or "") for p in papers if p.get("id")
        })
        status_counts = count_by(papers, "retrieval_status")
        source_type_counts = {}
        for _, kind in source_typed_papers:
            source_type_counts[kind] = source_type_counts.get(kind, 0) + 1
        coverage_summary = analysis.get("coverage_summary") if isinstance(analysis, dict) else None
        negative_evidence = analysis.get("negative_evidence") if isinstance(analysis, dict) else None
        alternative_angles = analysis.get("alternative_angles") if isinstance(analysis, dict) else None
        coverage_audit = analysis.get("coverage_audit", []) if isinstance(analysis, dict) else []
        coverage_records = analysis.get("coverage_records", []) if isinstance(analysis, dict) else []
        claim_verdicts = analysis.get("claim_verdicts", []) if isinstance(analysis, dict) else []
        reliability = analysis.get("reliability", {}) if isinstance(analysis, dict) else {}
        provisional_gaps = analysis.get("provisional_gaps", []) if isinstance(analysis, dict) else []
        rejected_gaps = analysis.get("rejected_gaps", []) if isinstance(analysis, dict) else []
        relevant_innovation_count = sum(
            1 for item in innovations
            if not item.get("error")
            and (
                not item.get("source_type")
                or item.get("source_type") in {"paper_fulltext", "paper_abstract"}
            )
        )
        fallback_audit_count = sum(
            1 for item in coverage_audit
            if isinstance(item, dict)
            and item.get("audit_mode") == "deterministic_fallback"
            and item.get("source_type") in {"paper_fulltext", "paper_abstract"}
        )

        confidence_notes = []
        if len(innovations) == 0:
            confidence_notes.append("没有成功提取创新画像，本报告只能作为检索摘要，不能当作可靠空白结论。")
        if abstract_count > 0:
            confidence_notes.append(f"有 {abstract_count} 篇只基于摘要/元数据分析，相关空白需要读全文后复核。")
        if fallback_audit_count:
            confidence_notes.append(
                f"有 {fallback_audit_count} 篇仅完成本地确定性字段映射；保留原文引用，但覆盖和新颖性主张未完成语义裁决。"
            )
        if not gaps:
            confidence_notes.append("没有候选通过证据门和逐项评审；待验证或已否决想法不能作为开题结论。")
        for warning in reliability.get("warnings", []) or []:
            confidence_notes.append(clean(warning, 300))
        if not confidence_notes:
            confidence_notes.append("已有全文和创新画像支撑，但仍建议人工核对最接近工作。")

        direction_lines = []
        if direction.get('research_question'):
            direction_lines.append(f"- **本轮研究问题**：{clean(direction['research_question'])}")
        for label, key in (("研究领域", "research_domain"), ("目标任务", "target_task"),
                           ("方法大类", "method_category"), ("方法子类", "method_subcategory"),
                           ("方法组件", "method_component")):
            if direction.get(key):
                direction_lines.append(f"- **{label}**：{clean(direction.get(key))}")
        for detail in (direction.get("implementation_details") or [])[:6]:
            direction_lines.append(f"- **方案关注点**：{clean(detail)}")
        for claim in (direction.get("claims_to_verify") or [])[:8]:
            direction_lines.append(f"- **待核查主张（不是已确认事实）**：{clean(claim)}")
        if not direction_lines:
            direction_lines.append("- 未能稳定解析研究方向；建议把研究问题写得更具体。")

        md = f"""# {report_title}：证据审计与候选空白报告

> 读法：先看第 0 节结论，再看第 3 节证据审计和第 4 节正式候选。待验证或已否决候选不能直接作为开题依据。

## 0. 结论先行

- 研究问题：{clean(direction.get('research_question') or title_hint or '见方向解析')}。
- 判断范围：仅限本轮检索与可核查证据。完成处理或未发现反证，都不能证明该方向无人研究。
- 已有实现与剩余问题（模型综合，需结合下文证据核查）：{clean(coverage_summary or '本轮未形成可靠综合判断，请查看证据缺口与待验证项。', 1000)}
- 通过证据门与 Critic 的正式候选：**{len(gaps)} 个**。
- 被可靠性门拦截的未验证候选：**{len(provisional_gaps)} 个**。
- 未采纳的候选：**{len(rejected_gaps)} 个**；未采纳不等于已证明被现有工作覆盖，具体依据见第 5 节。
- 纳入资料条目：**{len(papers)} 条**；可用于覆盖判定的真实论文 **{len(fulltext_papers) + len(abstract_papers)} 篇**。
- 本次请求显式手选论文：**{selected_count} 篇**；这与本地库检索命中数分开统计。
- 成功提取创新画像：**{sum(not item.get('error') for item in innovations)} 条**，失败 **{sum(bool(item.get('error')) for item in innovations)} 条**；其中论文全文支撑 **{fulltext_count} 条**、论文摘要支撑 **{abstract_count} 条**；先验/仓库/模型摘要不作直接覆盖证据。
- 可信度提醒：{clean(' '.join(confidence_notes))}

## 1. 我对研究问题的理解

{chr(10).join(direction_lines)}

## 2. 相关论文地图

### 2.1 你选中的上下文论文
"""
        if selected_papers:
            md += "\n".join(paper_line(p) for p in selected_papers) + "\n"
        else:
            md += "- 本轮没有显式选中论文，主要依赖检索结果。\n"

        md += "\n### 2.2 可用于覆盖判定的论文全文\n"
        md += "\n".join(paper_line(p) for p in fulltext_papers) if fulltext_papers else "- 暂无带原文证据的论文全文。"
        md += "\n\n### 2.3 可用于低置信度覆盖判定的论文摘要\n"
        md += "\n".join(paper_line(p) for p in abstract_papers) if abstract_papers else "- 暂无仅摘要论文。"
        md += "\n\n### 2.4 不直接用于论文覆盖判定的资料\n"
        md += "\n".join(paper_line(p) for p in nonpaper_sources) if nonpaper_sources else "- 暂无先验、仓库或模型摘要条目。"

        ledger = analysis.get('paper_processing', [])
        if ledger:
            md += '\n\n### 本轮逐篇处理清单\n\n检索到或选中不等于已完成分析；以下保留每一条输入的状态。\n\n| 论文 ID | 标题 | 处理状态 | 说明 |\n|---|---|---|---|\n'
            labels={'not_analyzed':'未分析','extraction_failed':'提取失败','excluded':'暂排除','unassessed':'未完成审计','fulltext':'全文画像已审计','abstract':'摘要画像已审计'}
            for row in ledger:
                md += f"| {cell(row['paper_id'])} | {cell(row['title'])} | {labels[row['status']]} | {cell(row['reason'])} |\n"

        md += "\n\n### 2.5 分析底座统计\n"
        md += f"- 来源分布：{clean(json.dumps(source_counts, ensure_ascii=False), 500)}\n"
        md += f"- 本次请求显式选择：{selected_count} 篇；从选中库取回：{retrieved_selected_count} 条。\n"
        md += f"- 追加反证：{counterevidence_count} 条；本地知识库补入：{local_kb_count} 条；唯一记录总数：{total_unique_records}。\n"
        md += f"- 证据类型分布：{clean(json.dumps(source_type_counts, ensure_ascii=False), 500)}\n"
        md += f"- 解析状态分布：{clean(json.dumps(status_counts, ensure_ascii=False), 500)}\n"
        md += f"- 缺全文数量：{len(missing)}；这会直接影响空白判断的可靠性。\n"
        search_execution = direction.get('search_execution') or {}
        if search_execution:
            md += "\n#### 实际检索范围\n\n"
            for entry in search_execution.get('completed_queries', []):
                md += f"- `{clean(entry.get('query'))}`：来源返回 {entry.get('returned_records', 0)} 条记录（合并去重前）。\n"
                for source in entry.get('sources', []):
                    detail=f"成功，{source.get('returned_records',0)} 条" if source.get('status')=='ok' else f"请求失败（{source.get('error_type','unknown')}，HTTP {source.get('http_status') or '未知'}），不能视为零结果"
                    md += f"  - {clean(source.get('source'))}：{detail}。\n"
            for query in search_execution.get('unexecuted_queries', []):
                md += f"- 未执行（查询预算限制）：`{clean(query)}`。\n"
            md += "- 本轮按查询预算停止，并未证明已穷尽相关文献；当前不自动根据反证启动新一轮检索。\n"
        audited_count = sum(
            1 for item in coverage_audit
            if isinstance(item, dict)
            and item.get("assessment_status") == "audited"
            and item.get("source_type") in {"paper_fulltext", "paper_abstract"}
        )
        md += f"- 逐篇证据审计：{audited_count}/{relevant_innovation_count} 篇相关画像；未完成审计的论文可能隐藏反证，可靠性门会阻止正式候选。\n"
        md += f"- 其中本地确定性 fallback：{fallback_audit_count} 篇；fallback 只保留待核查证据，不确认覆盖或候选空白。\n"
        md += f"- 矩阵真实归类论文数：{reliability.get('matrix_assigned_papers', 0)}；全空矩阵不代表文献为空。\n"
        md += "- 研判顺序：先看已有工作是否直接/部分覆盖，再看窄义差异；不能从未归类单元格反推领域无人研究。\n"

        if coverage_summary or negative_evidence or alternative_angles:
            md += "\n### 2.6 模型自带的覆盖/反证信息\n"
            if coverage_summary:
                from src.analysis.report_text import readable_text
                md += readable_text(coverage_summary) + '\n'
            for item in listify(negative_evidence)[:6]:
                md += f"- **反证/保守提醒**：{clean(item, 500)}\n"
            for item in listify(alternative_angles)[:6]:
                md += f"- **备选角度**：{clean(item, 500)}\n"

        md += "\n\n## 3. 逐篇证据审计与已覆盖路线\n\n"
        if claim_verdicts:
            md += "### 3.1 待核查主张的综合裁决\n\n"
            for verdict in listify(claim_verdicts):
                if isinstance(verdict, dict):
                    claim = verdict.get("claim") or verdict.get("claim_id") or "未命名主张"
                    status = verdict.get("verdict") or verdict.get("status") or "未裁决"
                    reason = verdict.get("reason") or verdict.get("rationale") or ""
                    md += f"- **{clean(claim, 220)}**：{clean(status)}。{clean(reason, 420)}\n"
                else:
                    md += f"- {clean(verdict, 500)}\n"

        if coverage_audit:
            md += "\n### 3.2 论文级方法关系与证据强度\n\n"
            md += "| 论文 | 方法关系 | 对待核查主张的裁决 | 证据强度 |\n"
            md += "|---|---|---|---|\n"
            for item in coverage_audit:
                if not isinstance(item, dict):
                    continue
                pid = str(item.get("paper_id") or "")
                relations = ", ".join(item.get("method_relations") or []) or "未分类"
                verdict_text = "; ".join(
                    f"{a.get('claim_id', '?')}={a.get('verdict', '未裁决')}"
                    for a in (item.get("claim_assessments") or [])
                    if isinstance(a, dict)
                ) or item.get("audit_warning") or "无主张级结果"
                evidence_levels = {
                    ev.get("evidence_level") for ev in (item.get("evidence") or [])
                    if isinstance(ev, dict) and ev.get("evidence_level")
                }
                strength = ", ".join(sorted(evidence_levels)) or "无可引用证据"
                md += f"| {cell(paper_title(pid), 80)} | {cell(relations, 80)} | {cell(verdict_text, 180)} | {cell(strength, 80)} |\n"

        if coverage_records and analysis.get('analysis_scope') == 'research_direction':
            md += "\n### 3.3 当前方向的主张覆盖记录\n\n"
            md += "| 论文 | 主张 ID | 方法关系 | 原文证据 ID |\n|---|---|---|---|\n"
            for record in coverage_records:
                md += (f"| {cell(paper_title(record['paper_id']))} | {cell(', '.join(record['claim_ids']))} | "
                       f"{cell(', '.join(record['method_relations']))} | {cell(', '.join(record['evidence_ids']))} |\n")
            md += "\n该表只记录当前主张的已有证据；不把未归类的方法或缺失的材料视为未研究的方向。\n"
        md += "\n### 3.4 创新画像摘要\n\n"
        if innovations:
            md += "| 论文 | 阶段/方法 | 提取到的创新点 | 证据强度 |\n"
            md += "|---|---|---|---|\n"
            for profile in innovations:
                pid = profile.get("paper_id", "")
                method = profile.get("method_subcategory") or profile.get("method_component") or "未分类"
                detail = profile.get("innovation_detail") or profile.get("contribution") or "未提取到清晰创新点"
                strength = ('提取失败' if profile.get('error') else '背景资料' if profile.get('source_type') in {'prior_knowledge','survey_or_repository','model_generated_summary'} else '全文' if profile.get('has_fulltext') else '摘要/元数据')
                md += f"| {cell(paper_title(pid), 70)} | {cell(method, 40)} | {cell(detail, 140)} | {strength} |\n"
        else:
            md += "没有提取到创新画像，因此不能可靠判断哪些路线已经被覆盖。\n"

        md += "\n## 4. 通过证据门与 Critic 的窄义候选\n\n"
        if gaps:
            md += "| 排名 | 候选空白 | 状态 | 核心想法 | 可行性 | 新颖性 | 难度 | 置信度 |\n"
            md += "|---|---|---|---|---|---|---|---|\n"
            for i, gap in enumerate(gaps, 1):
                md += (
                    f"| {i} | {cell(gap.get('name') or '未命名', 60)} | {cell(gap.get('candidate_status'), 30)} | "
                    f"{cell(gap.get('description'), 160)} | {cell(gap.get('feasibility'))}/5 | "
                    f"{cell(gap.get('novelty'))}/5 | {cell(gap.get('difficulty'))}/5 | {cell(gap.get('confidence'))} |\n"
                )
            md += "\n"
            for i, gap in enumerate(gaps, 1):
                md += f"### 4.{i} {clean(gap.get('name') or '未命名空白')}\n\n"
                md += f"- **一句话解释**：{clean(gap.get('description'), 520)}\n"
                if gap.get("why_original_may_be_weak"):
                    md += f"- **为什么这个空白可能仍不够好**：{clean(gap.get('why_original_may_be_weak'), 420)}\n"
                if gap.get("coverage_risk"):
                    md += f"- **可能已被覆盖的风险**：{clean(gap.get('coverage_risk'), 420)}\n"
                md += f"- **相对最近工作的窄义新颖性依据**：{clean(gap.get('novelty_basis'), 520)}\n"
                if gap.get("task_specific_contribution"):
                    md += f"- **目标任务中的具体贡献**：{clean(gap.get('task_specific_contribution'), 520)}\n"
                md += "- **最低判别实验**：\n"
                md += "\n".join(experiment_lines(gap.get("decisive_experiment"))) + "\n"
                if gap.get("alternative_framing"):
                    md += f"- **备选研究表述**：{clean(gap.get('alternative_framing'), 420)}\n"
                nearest = gap.get("nearest_works", []) or []
                if nearest:
                    md += "- **最接近工作/需要核对**：\n"
                    for nw in nearest[:5]:
                        nw_pid = nw.get("paper_id", "")
                        diff = nw.get("difference") or nw.get("description") or "需要人工确认差异"
                        overlap = nw.get("counterevidence_level") or "unclassified"
                        md += f"  - [{clean(overlap, 40)}] {clean(paper_title(nw_pid), 120)}：{clean(diff, 220)}\n"
                evs = gap.get("evidence", []) or []
                if evs:
                    md += "- **证据片段**：\n"
                    for ev in evs[:3]:
                        md += f"  - {evidence_text(ev)}\n"
                req_evs = listify(gap.get("required_evidence"))
                if req_evs:
                    md += "- **必须补读/补实验的证据**：\n"
                    for ev in req_evs[:4]:
                        md += f"  - {clean(ev, 260)}\n"
                md += "\n"
        else:
            md += "没有候选同时通过矩阵证据门、最近工作核查和 Critic 逐项裁决。\n"

        md += "\n## 5. 未进入正式结论的候选\n\n"
        if provisional_gaps:
            md += "### 5.1 待验证草案\n\n"
            md += "这些想法可以用于补检索或消融设计，但当前不能声称为研究空白。\n\n"
            for gap in provisional_gaps:
                reasons = listify(gap.get("reliability_notes"))
                if gap.get("critic_reason"):
                    reasons.append(gap.get("critic_reason"))
                fallback = gap.get("coverage_risk") or "证据不足"
                reason_text = "；".join(str(reason) for reason in reasons) or fallback
                md += f"\n### {clean(gap.get('name') or '未命名草案', 120)}\n\n"
                if gap.get('description'):
                    md += clean(gap['description']) + '\n\n'
                md += f"**为什么还不能确认**：{clean(reason_text)}\n\n"
                for evidence_needed in listify(gap.get('required_evidence')):
                    md += f"  - 待补证据：{clean(evidence_needed)}\n"
                for work in listify(gap.get('nearest_works')):
                    if isinstance(work,dict):md += f"  - 最近工作：{clean(work.get('paper_id'))}；待核查差异：{clean(work.get('difference'))}\n"
                for line in experiment_lines(gap.get("decisive_experiment")):
                    md += line + "\n"
        else:
            md += "- 没有待验证草案。\n"
        if rejected_gaps:
            md += "\n### 5.2 未采纳候选及理由\n\n"
            for gap in rejected_gaps:
                contradicted = ", ".join(gap.get("contradicted_by") or [])
                critic_suggested = ", ".join(gap.get("critic_contradicted_by") or [])
                reason = gap.get("critic_reason") or gap.get("coverage_risk") or gap.get("why_original_may_be_weak")
                suffix = f"；直接反证论文：{contradicted}" if contradicted else ""
                if critic_suggested:
                    suffix += f"；Critic建议核查：{critic_suggested}"
                md += f"- **{clean(gap.get('name') or '未命名候选', 120)}**：{clean(str(reason or '已有证据覆盖') + suffix, 520)}\n"
        else:
            md += "\n- 没有被明确否决的候选。\n"

        md += "\n## 6. 缺全文/低置信度来源\n\n"
        if missing:
            md += "这些论文目前只基于摘要或元数据参与分析，结论需要读全文后复核。\n\n"
            for m in missing:
                md += paper_line(m) + "\n"
                if m.get("url"):
                    md += f"  - 链接：{m['url']}\n"
        else:
            md += "- 本轮 MonitorAgent 没有报告缺全文论文；这不等同于检索范围已经穷尽。\n"

        md += "\n## 7. 推荐下一步\n\n"
        if gaps:
            md += "1. 只对第 4 节正式候选开展最低判别实验；实验前逐条核对其最近工作差异和引用证据。\n"
            md += "2. 按每个候选的最低判别实验设置最近工作基线、最小改动、指标和停止条件，保持数据与训练预算可比。\n"
        else:
            md += "1. 当前没有可直接开题的正式候选；先处理第 5 节草案所缺的全文、最近工作或任务证据，不要从全空矩阵反推新颖性。\n"
            md += "2. 围绕第 1 节研究问题，区分已有实现、尚缺证据和需要收窄的假设；不要为了产生结果而换成无关的创新方向。\n"
        md += "3. 对摘要级或未审计论文补齐全文后重新运行；新证据必须重新触发覆盖审计和 Critic 裁决。\n"
        md += "4. 外部二次研判应接收逐篇证据审计、正式/暂缓/否决状态和原文证据 ID，而不只接收最终候选段落。\n"
        md += "5. 若从结果得到新点子，在同一会话说明新机制、来源方向和要核查的问题，使用研究 Agent 补检索；讨论结果模式不执行检索。\n"

        critic = analysis.get("critic") if isinstance(analysis, dict) else None
        if critic:
            warnings = critic.get("warnings", []) or []
            suggestions = critic.get("suggestions", []) or []
            gap_reviews = critic.get("gap_reviews", []) or []
            if gap_reviews or warnings or suggestions:
                md += "\n## 8. Critic 逐项裁决与过度推断检查\n\n"
                for review in gap_reviews[:10]:
                    if not isinstance(review, dict):
                        continue
                    label = review.get("gap_name") or review.get("gap_id") or "未命名候选"
                    md += f"- **{clean(label, 120)} → {clean(review.get('verdict') or '未裁决')}**：{clean(review.get('reason'), 420)}\n"
                for warning in warnings[:5]:
                    md += f"- 风险：{clean(warning, 240)}\n"
                for suggestion in suggestions[:5]:
                    md += f"- 建议：{clean(suggestion, 240)}\n"

        md += "\n## 附录：原始方向解析\n\n"
        md += "```json\n" + json.dumps(direction, ensure_ascii=False, indent=2) + "\n```\n"
        matrix = analysis.get("matrix", {}) if isinstance(analysis, dict) else {}
        if matrix:
            md += "\n## 附录：原始创新矩阵\n\n"
            md += "```json\n" + json.dumps(matrix, ensure_ascii=False, indent=2) + "\n```\n"

        md += f"\n---\n*AI Reader 自动生成于 {ts}*\n"
        from src.analysis.report_layout import reading_edition
        md = reading_edition(md, direction, analysis, papers, innovations, missing)
        with report_path.open('x', encoding='utf-8') as output:
            output.write(md)
        return report_path

    def _collect_evidence(self, innovations: list[dict]) -> list[dict]:
        evidence = []
        for profile in innovations:
            paper_id = profile.get("paper_id", "")
            for ev in profile.get("evidence", []) or []:
                item = dict(ev) if isinstance(ev, dict) else {"quote": str(ev)}
                item["paper_id"] = paper_id
                evidence.append(item)
        return evidence

    def _save_gap_analysis(self, session_id: str, direction: dict,
                           papers: list[dict], innovations: list[dict],
                           analysis: dict, evidence: list[dict],
                           report_path: Path,
                           selected_paper_ids: list[str] | None = None):
        try:
            gaps = analysis.get("gaps", []) if isinstance(analysis, dict) else []
            confidences = [g.get("confidence") for g in gaps if isinstance(g.get("confidence"), (int, float))]
            confidence = sum(confidences) / len(confidences) if confidences else None
            selected_ids = list(dict.fromkeys(selected_paper_ids or []))
            source_counts = {}
            for paper in papers:
                source = str(paper.get("source") or "unknown")
                source_counts[source] = source_counts.get(source, 0) + 1
            search_log = {
                "papers_found": len(papers),
                "selected_paper_ids": selected_ids,
                "inherited_paper_ids": analysis.get('inherited_paper_ids', []),
                "paper_processing": analysis.get('paper_processing', []),
                "selection_provenance": {
                    "explicitly_selected_this_request": len(selected_ids),
                    "retrieved_from_selected_library": source_counts.get("selected_library", 0),
                    "added_as_counterevidence": source_counts.get("local_counterevidence", 0),
                    "added_from_local_kb": source_counts.get("local_kb", 0),
                    "total_unique_records": len({str(p.get("id") or "") for p in papers if p.get("id")}),
                },
                "papers": [
                    {"id": p.get("id"), "title": p.get("title"),
                     "source": p.get("source"),
                     "retrieval_status": p.get("retrieval_status")}
                    for p in papers
                ],
                "innovations_extracted": sum(not item.get('error') for item in innovations),
                "reliability": analysis.get("reliability", {}),
                "coverage_records": analysis.get("coverage_records", []),
                "provisional_gaps": analysis.get("provisional_gaps", []),
                "rejected_gaps": analysis.get("rejected_gaps", []),
            }
            self.db.execute(
                """INSERT INTO gap_analyses
                   (id, session_id, direction, search_log_json, matrix_json,
                    gaps_json, evidence_json, report_path, confidence)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), session_id,
                 json.dumps(direction, ensure_ascii=False),
                 json.dumps(search_log, ensure_ascii=False),
                 json.dumps(analysis.get("matrix", {}), ensure_ascii=False),
                 json.dumps(gaps, ensure_ascii=False),
                 json.dumps(evidence, ensure_ascii=False),
                 str(report_path), confidence),
            )
        except Exception as e:
            logger.warning(f"Failed to save gap analysis: {e}")
            raise
