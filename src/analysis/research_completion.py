"""Bounded follow-up research, performed by the agent before report delivery."""
import json
from src.agents.base import AgentResult


def pending_material(papers, profiles, analysis):
    by_id={p.get('paper_id'):p for p in profiles}
    audited={p.get('paper_id') for p in analysis.get('coverage_audit',[])
             if p.get('assessment_status')=='audited' and p.get('audit_mode')!='deterministic_fallback'}
    return [{'id':p['id'],'title':p.get('title',''),'abstract':(p.get('abstract') or '')[:900],
             'reason':'未提取' if p['id'] not in by_id else '抽取失败' if by_id[p['id']].get('error') else '未完成证据核查'}
            for p in papers if p['id'] not in by_id or by_id[p['id']].get('error') or p['id'] not in audited]


def select_retry_papers(papers, requested, failed_ids, pending_ids, attempted):
    by_id={p['id']:p for p in papers}
    ids=list(dict.fromkeys([pid for pid in requested if isinstance(pid,str) and pid in by_id]+sorted(failed_ids)))
    # Preserve model relevance order within each group; already-read papers cannot crowd out unread counterexamples.
    ids.sort(key=lambda pid:pid not in pending_ids)
    return [by_id[pid] for pid in ids if pid not in attempted][:8]


async def complete_research(owner, ctx, direction, papers, profiles, analysis, parse_data):
    from src.tools.search_tools import search_all, merge_and_deduplicate
    from src.agents.parse_agent import ParseAgent
    limits = ctx.config.get('research', {})
    rounds = max(0, min(2, int(limits.get('followup_rounds', 2))))
    query_limit = max(1, min(8, int(limits.get('followup_queries', 6))))
    new_limit = max(1, min(20, int(limits.get('followup_papers', 8))))
    log = []
    attempted=set()
    executed = {r['query'] for r in direction.get('search_execution', {}).get('completed_queries', [])}
    for round_no in range(rounds):
        candidates = analysis.get('gaps', []) + analysis.get('provisional_gaps', [])
        pending = [q for q in direction.get('search_queries', []) if q not in executed]
        failed_ids = {p.get('paper_id') for p in profiles if p.get('error')}
        unread=pending_material(papers,profiles,analysis)
        if not candidates and not pending and not unread:
            break
        run = await owner._create_agent_run(ctx.session_id, 'ResearchFollowup', {}, '补查候选与最近工作')
        await owner._progress(ctx.session_id, 'ResearchFollowup', 'planning',
                              '正在把候选缺少的文献核查转为检索任务')
        plan = await owner.llm.chat_json([
            {'role':'system', 'content':
             '你负责执行论文研究后的补查。只输出JSON。给出最多6条具体英文检索式 queries，'
             '优先核查候选的最近工作、同义机制和反例；同时给 retry_paper_ids，最多8篇已有资料。'
             '从 pending_papers 中优先选可能直接覆盖候选的未读论文，ID只能取自输入。不要因未读就认定无关。'
             '输入里的训练、代码实现和实验不在本次文献Agent工具范围，不要伪称执行过。'
             '不要把检索和读论文推给用户。已执行的检索不重复。'},
            {'role':'user','content':json.dumps({'question':direction.get('research_question'),
             'candidates':candidates, 'already_searched':sorted(executed),
             'failed_papers':sorted(failed_ids),'pending_papers':unread,
             'available_papers':[{'id':p['id'],'title':p.get('title','')} for p in papers],
             'already_attempted':sorted(attempted)}, ensure_ascii=False)}])
        queries = list(dict.fromkeys([q for q in plan.get('queries', []) if isinstance(q, str) and q.strip()] + pending))
        queries = [q for q in queries if q not in executed][:query_limit]
        found = []
        search_cfg = ctx.config.get('search', {})
        for query in queries:
            await owner._progress(ctx.session_id,'ResearchFollowup','search',f'补检索：{query}')
            results = await search_all(query, 10,
                enable_semantic_scholar=search_cfg.get('enable_semantic_scholar', True),
                enable_arxiv=search_cfg.get('enable_arxiv', True))
            found.extend(results); executed.add(query)
            entry = {'query':query, 'returned_records':len(results), 'sources':getattr(results,'diagnostics',[])}
            direction.setdefault('search_execution',{}).setdefault('completed_queries',[]).append(entry)
            log.append({'round':round_no+1, 'action':'search', **entry})
        existing = {p['id'] for p in papers}
        found = [owner._hydrate_paper_from_db(p.model_dump()) for p in await merge_and_deduplicate(found)]
        found = [p for p in found if p['id'] not in existing]
        total_new=max(0,min(100,int(limits.get('max_new_papers',30))))
        remaining=max(0,total_new-direction.get('search_execution',{}).get('new_papers',0))
        found=found[:remaining]
        if len(found) > new_limit:
            ranking = await owner.llm.chat_json([
                {'role':'system','content':'按与当前候选直接相关的程度选论文。只输出JSON selected_ids 字符串数组，最多'+str(new_limit)+'项，只能使用输入ID。'},
                {'role':'user','content':json.dumps({'question':direction.get('research_question'),'candidates':candidates,
                 'papers':[{'id':p['id'],'title':p['title'],'abstract':(p.get('abstract') or '')[:900]} for p in found]},ensure_ascii=False)}])
            chosen = set(ranking.get('selected_ids', []))
            found = [p for p in found if p['id'] in chosen][:new_limit]
        for paper in found:
            await owner.kb.upsert_paper(paper)
        papers.extend(found)
        direction.setdefault('search_execution',{})['new_papers']=direction.get('search_execution',{}).get('new_papers',0)+len(found)
        retry_papers = select_retry_papers(papers,plan.get('retry_paper_ids') or [],failed_ids,
                                           {p['id'] for p in unread},attempted)
        attempted.update(p['id'] for p in retry_papers)
        targets = list({p['id']:p for p in found + retry_papers}.values())
        if targets:
            async def progress(detail):
                await owner._progress(ctx.session_id,'ResearchFollowup','parse',f"补读论文：{detail['title']}")
            parsed = await ParseAgent(owner.db, progress).run(ctx, {'papers':targets,'deep_parse_top_k':len(targets)})
            added = await owner._extract_innovations(ctx, parsed, targets)
            by_id = {p['paper_id']:p for p in profiles}
            for profile in added:
                pid = profile['paper_id']
                if not profile.get('error'):
                    by_id[pid] = profile
                    await owner.kb.save_innovation_profile(pid, profile)
                elif pid not in by_id:
                    by_id[pid] = profile
            profiles = list(by_id.values())
            for key in ('parsed','metadata_only'):
                parse_data[key] = list(dict.fromkeys(parse_data.get(key,[]) + parsed.data.get(key,[])))
            done = set(parsed.data.get('parsed',[]) + parsed.data.get('metadata_only',[]))
            parse_data['deferred'] = [pid for pid in parse_data.get('deferred',[]) if pid not in done]
        # Re-synthesize with the new retrieval facts even if providers returned nothing.
        direction['search_execution']['unexecuted_queries'] = [q for q in direction.get('search_queries',[]) if q not in executed]
        direction['search_execution']['stop_reason'] = 'bounded_followup'
        await owner._progress(ctx.session_id,'ResearchFollowup','reanalyze',
                              '正在重新比较补查后的证据，检查候选与最近工作的差异')
        analysis = owner._enforce_analysis_reliability(direction, profiles,
                   owner._normalize_analysis(await owner._analyze_gaps(direction, profiles)))
        log.append({'round':round_no+1,'action':'read','new_papers':len(found),'retried_papers':len(retry_papers)})
        await owner._finish_agent_run(run,'ResearchFollowup',ctx.session_id,
              AgentResult(status='completed',data={'queries':len(queries),'new_papers':len(found),'retried_papers':len(retry_papers)}))
        if not found and not retry_papers:
            break  # Do not loop on the same unavailable providers without new evidence.
    analysis['followup_actions'] = log
    return papers, profiles, analysis, parse_data
