"""A bounded tool-selection loop: the model chooses the next research action."""
import json
import uuid
from copy import deepcopy
from src.agents.base import AgentResult
from src.analysis.research_plan import plan_for, scoped_papers
from src.runtime.settings import atomic_write
from src.analysis.research_coverage import pending_reading, reading_coverage, local_fulltext, local_baseline_papers, model_key, matches_model


def normalize_decision(value):
    """A JSON object is not yet a valid tool request; never iterate raw fields."""
    errors=[]
    if not isinstance(value,dict):
        value={};errors.append('规划结果不是对象')
    action=value.get('action')
    action=action.strip().lower() if isinstance(action,str) else None
    if action not in {'retrieve','search','lookup','read','analyze','deliver'}:
        action=None;errors.append('行动名称无效')
    result={'action':action,'reason':str(value.get('reason') or '')[:2000],
            'progress_summary':str(value.get('progress_summary') or '')[:600]}
    for field,limit in (('paper_ids',100),('queries',3)):
        raw=value.get(field)
        if raw is None:raw=[]
        elif isinstance(raw,str):
            raw=[raw];errors.append(field+' 使用了单个字符串，已规范为列表')
        elif not isinstance(raw,list):
            raw=[];errors.append(field+' 类型无效')
        if any(not isinstance(item,str) for item in raw):errors.append(field+' 包含非字符串项目，已忽略')
        result[field]=list(dict.fromkeys(item.strip() for item in raw if isinstance(item,str) and item.strip()))[:limit]
    required='queries' if action in {'search','retrieve'} else 'paper_ids' if action in {'read','lookup'} else None
    if required and not result[required]:
        result['action']=None;errors.append(required+' 缺少有效项目')
    result['validation_errors']=errors
    return result


async def execute_research(owner, ctx, direction, selected_ids, *, resume_snapshot=None):
    from src.agents.parse_agent import ParseAgent
    from src.analysis.question_evidence import analyze_question
    from src.analysis.paper_ledger import paper_ledger
    restored=deepcopy(resume_snapshot) if resume_snapshot else None
    if restored:direction.update(restored['direction'])
    plan=plan_for(direction)
    provided_only=plan.get('source_scope')=='provided'
    papers,excluded=scoped_papers(restored['papers'] if restored else owner._selected_papers(selected_ids),direction)
    profiles=[p for p in restored['innovations'] if p['paper_id'] in {paper['id'] for paper in papers}] if restored else []
    analysis=None
    parse_data=restored['parse_data'] if restored else {'parsed':[],'metadata_only':[],'deferred':[],'failed':[]}
    decisions=restored['decisions'] if restored else []
    searched={query for entry in decisions if entry['action']=='search' for query in entry.get('queries',[])}
    retrieved={query for entry in decisions if entry['action']=='retrieve' for query in entry.get('queries',[])}
    retriever=getattr(owner,'retriever',None)
    attempted={}
    for entry in decisions:
        if entry['action']=='read':
            for pid in entry.get('paper_ids',[]):attempted[pid]=attempted.get(pid,0)+1
    looked_up={pid for entry in decisions if entry['action']=='lookup' for pid in entry.get('paper_ids',[])}
    diagnostics=deepcopy(direction['search_execution']) if restored else {
        'completed_queries':[],'excluded_by_date':excluded,'new_papers':0,'deferred_new_records':0,'exhaustive':False}
    direction['search_execution']=diagnostics
    limits=ctx.config.get('research',{})
    max_papers=max(min(100,len(selected_ids)),max(1,min(100,int(limits.get('max_new_papers',60)))))
    max_reads=owner._parse_budget([{}]*max_papers,ctx.config.get('search',{}).get('deep_parse_top_k',60),ctx.config)
    max_queries=max(1,min(18,int(ctx.config.get('search',{}).get('max_rounds',6))*2))
    max_steps=max(4,min(24,int(limits.get('max_agent_steps',16))))
    if len(papers)>max_papers or len(attempted)>max_reads or len(searched)>max_queries:
        raise ValueError('研究检查点超出当前资料或行动预算，不能扩大预算续跑')
    root=ctx.workspace_root/'.agent_history/research'
    root.mkdir(parents=True,exist_ok=True)
    checkpoint=root/('adaptive_'+uuid.uuid4().hex+'.json')
    def save():
        coverage=reading_coverage(papers,profiles,selected_ids,attempted,ctx.workspace_root,max_reads)
        diagnostics['reading_coverage']=coverage
        if analysis is not None:
            analysis['reading_coverage']=coverage
            if coverage['status']=='partial' and plan['task_type']!='gap_discovery':analysis['answer_status']='partial'
        atomic_write(checkpoint,json.dumps({'direction':direction,'papers':papers,'innovations':profiles,
            'analysis':analysis or {},'parse_data':parse_data,'decisions':decisions,
            'resumed_from_checkpoint':bool(restored)},ensure_ascii=False,indent=2))
    for turn in range(len(decisions),max_steps):
        pending=pending_reading(papers,profiles,selected_ids,attempted,ctx.workspace_root)
        request_slots=max(0,getattr(owner.llm,'max_calls',200)-getattr(owner.llm,'calls',0)-7)
        readable=[p for p in pending if p['id'] in attempted or len(attempted)<max_reads]
        # A closed-source follow-up ends after its sources have been read (or
        # exhausted) and analyzed. Do not pay for a planner call to rediscover
        # that completion, or turn missing fields into a new global survey.
        if provided_only and analysis is not None and (not readable or not request_slots):
            coverage=reading_coverage(papers,profiles,selected_ids,attempted,ctx.workspace_root,max_reads)
            complete=coverage['status']=='complete' and analysis.get('answer_status')=='answered_in_retrieved_sources' and not analysis.get('missing_information')
            diagnostics['stop_reason']='provided_sources_answered' if complete else 'provided_sources_exhausted'
            entry={'step':turn+1,'action':'deliver','reason':'已分析本次指定资料；交付已核实结果及未完成项，不扩大文献范围',
                   'progress_summary':'当前论文核对结束','queries':[],'paper_ids':[], 'scope_override':True}
            decisions.append(entry)
            run=await owner._create_agent_run(ctx.session_id,'ResearchPlanner',{},entry['reason'],label='完成本轮核对')
            await owner._finish_agent_run(run,'ResearchPlanner',ctx.session_id,AgentResult(status='completed',data=entry))
            save()
            break
        state={'research_plan':plan,'searched_queries':sorted(searched),
            'remaining_model_requests':max(0,getattr(owner.llm,'max_calls',200)-getattr(owner.llm,'calls',0)),
            'remaining_queries':max_queries-len(searched),'remaining_paper_slots':max_papers-len(papers),
            'remaining_read_slots':max_reads-len(attempted), 'remaining_steps':max_steps-turn,
            'allowed_actions':(['retrieve'] if retriever else [])+(['lookup','read','analyze','deliver'] if provided_only else ['search','lookup','read','analyze','deliver']),
            'local_index':retriever.status() if retriever else {'documents':0},
            'remaining_local_queries':max(0,6-len(retrieved)),
            'local_evidence':[{'query':entry['query'],'hits':[
                {**{k:hit[k] for k in ('paper_id','title','section','source_path','document_sha256','start','end','methods')},
                 'excerpt':hit.get('matched_text','')[:1600]} for hit in entry['hits']]
                } for entry in diagnostics.get('local_retrieval',[])[-3:]],
            'papers':[{'id':p['id'],'title':p.get('title'),'year':p.get('year'),
                       'publication_date':p.get('publication_date'),'temporal_status':p.get('temporal_status'),
                       'local_fulltext_available':local_fulltext(p,ctx.workspace_root),'selected':p['id'] in selected_ids,
                       'abstract':(p.get('abstract') or '')[:650],'read_attempts':attempted.get(p['id'],0)} for p in papers],
            'analysis':{k:(analysis or {}).get(k) for k in ('answer_status','benchmark_summary','answers','missing_information','gaps','provisional_gaps')},
            'recent_actions':decisions[-4:],
            'reading_coverage':reading_coverage(papers,profiles,selected_ids,attempted,ctx.workspace_root,max_reads),
            'priority_read_ids':[p['id'] for p in pending],
            'baseline_followups':(analysis or {}).get('baseline_followups',[]),
            'baseline_traces':diagnostics.get('baseline_traces',[])}
        state['read_results']=[{'paper_id':p['paper_id'],'source_type':p.get('source_type'),
            'error':str(p.get('error') or '')[:400],'findings':p.get('findings',[])[:3],
            'numerical_rows':len(p.get('benchmark_results',[])), 'reading_input':{
                k:v for k,v in p.get('reading_input',{}).items() if k in {'mode','input_complete','source_chars','input_chars'}}} for p in profiles]
        successful={p['paper_id'] for p in profiles if not p.get('error')}
        failures={p['paper_id']:p for p in parse_data.get('failed',[]) if isinstance(p,dict) and p.get('paper_id') not in successful}
        for pid,failure in failures.items():
            if any(p['paper_id']==pid for p in state['read_results']):continue
            state['read_results'].append({'paper_id':pid,'source_type':'local_pdf_parse_failed',
                'error':str(failure.get('reason') or 'PDF解析失败')[:400],'findings':[],'numerical_rows':0})
        state['looked_up_papers']=sorted(looked_up)
        run=await owner._create_agent_run(ctx.session_id,'ResearchPlanner',{},'根据当前证据选择下一步',label='决定下一步')
        decision=await owner.llm.chat_json([
            {'role':'system','content':
             '你控制论文研究Agent下一步行动，每轮重新判断，不必按固定流程走。只输出JSON '
             '{progress_summary,action,reason,queries:[],paper_ids:[]}。action只能来自allowed_actions。'
             'retrieve用1至3条自然语言queries检索本机论文全文，可给paper_ids限定范围；支持中英文语义和关键词混合检索。'
             '本机索引有资料时，优先用retrieve定位当前问题的原文章节与完整表格，然后read核对对应论文。'
             'local_evidence是可追溯原文片段，不是整篇阅读或最终结论；不要只看top-k就宣称覆盖全部选中论文。'
             '已成功读取全文且结果足够时不要重复retrieve。片段读取缺信息时可换具体查询补定位；同一查询不重复，最多6条。'
             'search可给1至3条英文检索式；read选择输入已有paper_ids，可先读后搜、补读或继续搜；'
             'analyze整理已读证据并核对问题；deliver在本轮已能回答或确有边界时结束。'
             'lookup用paper_ids核对已有论文公开日期和PDF入口，适合日期不明或全文链接缺失；每篇最多查一次。'
             'read_results展示真实提取结果；成功读取不等于找到所需数值，失败原因要用于下一步判断。'
             'source_scope=provided 时只核对输入论文，不得搜索其他论文、恢复旧排名任务或追查别的模型。'
             '每篇分别回答当前用户问题；协议差异作说明，缺信息时交付已确认部分，不能无限寻找旁证。'
             'source_scope=open_literature 的最近/最新最低值问题至少执行一次限日期检索；不要只靠旧知识库。'
             '检索句保留目标任务，查最接近回答的原论文，不为了统一硬件而把已报告EPE都拒绝。'
             '时间不明的关键论文优先查发布日期，不把查不到数值改写为研究空白。'
             '数值比较检查两个组的原文数值；缺组优先补检索/补读该组。'
             'priority_read_ids中的指定论文、已有全文和原文追查目标应先完成读取，不把“阶段完成”当全部读完。'
             '开放检索时baseline_followups是比较表中更优的对照成绩，需查原论文并read，不能丢弃或算给引用它的论文。'
             'all_time_groups与近期检索时间窗分开；相应分组可比较较早模型，其他分组不能自动放宽。'
             '缺资料时你负责可执行的文献补查，不推给用户；读不到全文或预算耗尽时交付已确认部分及边界。'
             '同一检索不重复，读过失败论文最多重试一次；读完新资料要重新analyze再deliver。'
             '关注remaining预算，留出分析和报告费用。最后两步优先分析和交付。'},
            {'role':'user','content':json.dumps(state,ensure_ascii=False)}],purpose='analysis')
        decision=normalize_decision(decision)
        requested_action=decision['action']
        action=requested_action
        if action is None:
            action='read' if readable and request_slots and max_steps-turn>=2 else 'analyze' if analysis is None else 'deliver'
            decision['paper_ids']=[p['id'] for p in readable]
        if action=='retrieve' and (not retriever or len(retrieved)>=6):
            action='read' if readable else 'analyze' if analysis is None else 'deliver'
            decision['paper_ids']=[p['id'] for p in readable]
        traces=diagnostics.get('baseline_traces',[])
        trace_queries=[t['query'] for t in traces if t['status']=='search_pending' and t['query'] not in searched]
        if provided_only and (action=='search' or action=='analyze' and analysis is not None
                or action=='read' and not any(p['id'] in decision.get('paper_ids',[]) for p in readable)
                or action=='lookup' and not any(p['id'] in decision.get('paper_ids',[]) and p['id'] not in looked_up for p in papers)):
            action='read' if readable and request_slots and max_steps-turn>=2 else 'analyze'
            decision['paper_ids']=[p['id'] for p in readable]
        elif provided_only and action=='read':
            decision['paper_ids']=[p['id'] for p in readable if p['id'] in decision.get('paper_ids',[])]
        if action=='deliver' and readable and request_slots and max_steps-turn>=2:
            action='read';decision['paper_ids']=[p['id'] for p in readable]
        elif not provided_only and action=='deliver' and trace_queries and request_slots and len(searched)<max_queries and max_steps-turn>=3:
            action='search';decision['queries']=trace_queries[:2]
        elif action=='deliver' and analysis is None:
            action='analyze'
        # The final step cannot invalidate an existing analysis by fetching new
        # evidence that there is no remaining step to analyze. Partial delivery
        # is truthful; throwing away the report after another read is not useful.
        if max_steps-turn==1:
            action='analyze' if analysis is None else 'deliver'
        entry={'step':turn+1,'action':action,'reason':str(decision.get('reason') or ''),
               'progress_summary':str(decision.get('progress_summary') or ''),'queries':[], 'paper_ids':[]}
        if decision['validation_errors']:entry['planner_output_errors']=decision['validation_errors']
        decisions.append(entry)
        if action!=requested_action:
            entry['coverage_override']=requested_action
            entry['progress_summary']={'read':'继续读取尚未完成的关键论文','search':'追查比较表中更优成绩的原论文',
                                       'analyze':'整理本轮已读证据并保留未完成项','deliver':'交付已有分析并说明未完成资料'}[action]
        await owner._finish_agent_run(run,'ResearchPlanner',ctx.session_id,AgentResult(status='completed',data=entry))
        save()
        if action=='deliver':
            if analysis is None:
                entry['blocked']='新证据尚未分析'
                continue
            if not provided_only and plan['time_scope'] and not searched:
                entry['blocked']='近期问题尚未执行限日期检索'
                continue
            diagnostics['stop_reason']='agent_delivery'
            break
        if action=='search':
            queries=[q.strip() for q in decision.get('queries',[]) if isinstance(q,str) and q.strip() and q.strip() not in searched][:min(3,max_queries-len(searched))]
            if not queries:
                entry['blocked']='查询为空、重复或已达查询预算'
                continue
            search_direction=dict(direction,search_queries=queries)
            config={**ctx.config,'search':{**ctx.config.get('search',{}),'max_rounds':len(queries)},
                    'research':{**limits,'max_new_papers':max_papers}}
            run=await owner._create_agent_run(ctx.session_id,'SearchAgent',{'queries':queries},'检索能回答当前问题的论文',label='检索论文')
            found=await owner._search(plan['objective'],search_direction,config,[])
            searched.update(queries)
            log=search_direction['search_execution']
            diagnostics['completed_queries'].extend(log.get('completed_queries',[]))
            diagnostics['excluded_by_date'].extend(log.get('excluded_by_date',[]))
            diagnostics['deferred_new_records']+=log.get('deferred_new_records',0)
            existing={p['id'] for p in papers}
            additions=[p for p in found if p['id'] not in existing]
            admitted=[]
            for item in additions:
                if len(papers)+len(admitted)<max_papers:
                    admitted.append(item);continue
                replaceable=[p for p in papers if p['id'] not in attempted and p['id'] not in selected_ids and not p.get('benchmark_followup')]
                weakest=min(replaceable,key=lambda p:p.get('relevance_score',0.5),default=None)
                if weakest and item.get('relevance_score',0.5)>weakest.get('relevance_score',0.5):
                    papers.remove(weakest);admitted.append(item)
            diagnostics['deferred_new_records']+=len(additions)-len(admitted)
            papers.extend(admitted)
            for trace in traces:
                if trace['status']=='search_pending' and trace['query'] in queries:
                    trace['status']='searched'
                    for paper in papers:
                        if matches_model(paper.get('title',''),trace['model']):
                            paper['benchmark_followup']=True;trace.update(status='admitted',paper_id=paper['id'])
            for p in admitted:await owner.kb.upsert_paper(p)
            by_id={p['id']:p for p in found}
            for p in papers:
                fresh=by_id.get(p['id'],{})
                for key in ('publication_date','date_source','temporal_status','temporal_reason'):
                    if fresh.get(key):p[key]=fresh[key]
            if admitted or by_id:
                analysis=None
            diagnostics['new_papers']=len({p['id'] for p in papers}-set(selected_ids))
            entry['queries']=queries
            await owner._finish_agent_run(run,'SearchAgent',ctx.session_id,AgentResult(status='completed',data={'admitted':len(admitted),'total':len(papers)}))
        elif action=='retrieve':
            import asyncio
            from src.knowledge.paper_retrieval import library_papers
            eligible=papers if provided_only else library_papers(owner.db)
            if not provided_only:
                eligible=[dict(p,is_user_selected=False) for p in eligible]
                eligible,_=scoped_papers(eligible,direction)
            allowed={p['id'] for p in eligible}
            wanted=set(decision['paper_ids'])
            if wanted:allowed &= wanted
            queries=[q for q in decision['queries'] if q not in retrieved][:max(0,6-len(retrieved))]
            if not queries or not allowed:
                entry['blocked']='没有新的本地查询或当前范围内没有论文';continue
            run=await owner._create_agent_run(ctx.session_id,'LocalRetrieval',{'queries':queries},'定位论文库原文证据',label='检索论文库原文')
            hits=[]
            for query in queries:
                found=await asyncio.to_thread(retriever.search,query,sorted(allowed),6)
                hits.extend(found)
                diagnostics.setdefault('local_retrieval',[]).append({'query':query,'hits':found})
            retrieved.update(queries)
            entry['queries']=queries
            entry['paper_ids']=list(dict.fromkeys(h['paper_id'] for h in hits))
            current={p['id'] for p in papers}
            additions=[dict(p,is_user_selected=False,source='local_fulltext_index') for p in owner._selected_papers(entry['paper_ids']) if p['id'] not in current]
            additions=additions[:max(0,max_papers-len(papers))]
            if additions:
                papers.extend(additions);analysis=None
            await owner._finish_agent_run(run,'LocalRetrieval',ctx.session_id,AgentResult(status='completed',data={'hits':len(hits),'admitted':len(additions),'queries':queries}))
        elif action=='lookup':
            from src.tools.paper_source import lookup_paper_source
            wanted=set(p for p in decision.get('paper_ids',[]) if isinstance(p,str))
            targets=[p for p in papers if p['id'] in wanted and p['id'] not in looked_up][:8]
            if not targets:
                entry['blocked']='没有尚未核对来源的论文';continue
            run=await owner._create_agent_run(ctx.session_id,'SourceLookup',{},'核对论文发布日期和全文入口',label='核对来源')
            for paper in targets:
                updates,source_log=await lookup_paper_source(paper)
                looked_up.add(paper['id']);paper.update(updates)
                entry.setdefault('source_results',[]).append({'paper_id':paper['id'],'sources':source_log})
                if updates:await owner.kb.upsert_paper(paper)
            papers,new_excluded=scoped_papers(papers,direction)
            diagnostics['excluded_by_date'].extend(new_excluded)
            current_ids={p['id'] for p in papers};profiles=[p for p in profiles if p['paper_id'] in current_ids]
            analysis=None;entry['paper_ids']=[p['id'] for p in targets]
            await owner._finish_agent_run(run,'SourceLookup',ctx.session_id,AgentResult(status='completed',data={'checked':len(targets)}))
        elif action=='read':
            wanted=set(p for p in decision.get('paper_ids',[]) if isinstance(p,str))
            targets=[p for p in papers if p['id'] in wanted and attempted.get(p['id'],0)<2]
            remaining=max(0,min(max_reads-len(attempted),getattr(owner.llm,'max_calls',200)-getattr(owner.llm,'calls',0)-7))
            targets=([p for p in targets if p['id'] in attempted]+[p for p in targets if p['id'] not in attempted][:remaining])[:request_slots]
            if not targets:
                entry['blocked']='没有可读论文或已达读取预算'
                continue
            for p in targets:
                attempted[p['id']]=attempted.get(p['id'],0)+1
                await owner.kb.upsert_paper(p)
            run=await owner._create_agent_run(ctx.session_id,'ParseAgent',{'paper_ids':[p['id'] for p in targets]},'读取AI选定的论文',label='获取全文')
            async def progress(detail):
                await owner._progress(ctx.session_id,'ParseAgent','paper_progress','正在获取全文：'+detail['title'],detail)
            parsed=await ParseAgent(owner.db,progress).run(ctx,{'papers':targets,'deep_parse_top_k':len(targets)})
            await owner._finish_agent_run(run,'ParseAgent',ctx.session_id,parsed)
            agent='InnovationExtractor' if plan['task_type']=='gap_discovery' else 'QuestionExtractor'
            run=await owner._create_agent_run(ctx.session_id,agent,{},'提取当前问题所需的原文证据',label='读取问题证据')
            added=await owner._extract_innovations(ctx,parsed,targets,direction=direction)
            by_id={p['paper_id']:p for p in profiles}
            for profile in added:
                by_id[profile['paper_id']]=profile
                if not profile.get('error'):await owner.kb.save_innovation_profile(profile['paper_id'],profile)
            profiles=list(by_id.values())
            for key in ('parsed','metadata_only'):
                parse_data[key]=list(dict.fromkeys(parse_data[key]+parsed.data.get(key,[])))
            parse_data['failed'].extend(parsed.data.get('failed',[]))
            analysis=None
            entry['paper_ids']=[p['id'] for p in targets]
            await owner._finish_agent_run(run,agent,ctx.session_id,AgentResult(status='completed',data={'count':len(added)}))
        else:
            current={p['id']:p for p in papers}
            for profile in profiles:
                for key in ('publication_date','year','date_source'):
                    if current.get(profile['paper_id'],{}).get(key):
                        profile[key]=current[profile['paper_id']][key]
            run=await owner._create_agent_run(ctx.session_id,'AnalyzeAgent',{},'对照已读证据回答问题',label='比较与分析')
            if plan['task_type']=='gap_discovery':
                analysis=owner._enforce_analysis_reliability(direction,profiles,owner._normalize_analysis(await owner._analyze_gaps(direction,profiles)))
                from src.agents.critic_agent import CriticAgent
                candidates=analysis.get('gaps',[])+analysis.get('provisional_gaps',[])
                if candidates:
                    review=await CriticAgent(owner.llm).run(ctx,{'analysis':dict(analysis,gaps=candidates),'innovations':profiles})
                    if review.status!='completed':raise RuntimeError(review.error or '候选核查失败')
                    analysis['critic']=review.data
                    analysis=owner._apply_critic_verdicts(analysis,review.data,profiles)
            else:
                analysis=await analyze_question(owner.llm,direction,profiles)
                traces=diagnostics.setdefault('baseline_traces',[])
                for lead in ([] if provided_only else analysis.get('baseline_followups',[])):
                    if any(model_key(t['model'])==model_key(lead['model']) for t in traces):continue
                    trace={**lead,'status':'search_pending','query':' '.join(str(lead.get(k) or '') for k in ('model','dataset','metric'))}
                    traces.append(trace)
                    candidates,outside=scoped_papers(local_baseline_papers(owner,lead['model']),direction)
                    if outside and not candidates:trace['status']='outside_scope'
                    for candidate in candidates[:1]:
                        existing=next((p for p in papers if p['id']==candidate['id']),None)
                        if existing is None and len(papers)<max_papers:
                            papers.append(candidate);existing=candidate
                        if existing:
                            existing['benchmark_followup']=True;trace.update(status='admitted',paper_id=existing['id'])
                        else:trace['status']='paper_budget'
            analysis['paper_processing']=paper_ledger(papers,profiles,analysis.get('coverage_audit',[]),parse_data)
            await owner._finish_agent_run(run,'AnalyzeAgent',ctx.session_id,AgentResult(status='completed',data={k:analysis.get(k) for k in ('task_type','answer_status','missing_information')}))
        save()
    else:
        diagnostics['stop_reason']='agent_step_budget'
    if analysis is None:
        save()
        raise RuntimeError('研究行动预算已用完，但未完成新证据分析；资料已保存，未发布报告')
    analysis.update(research_plan=plan,agent_decisions=decisions)
    analysis['paper_processing']=paper_ledger(papers,profiles,analysis.get('coverage_audit',[]),parse_data)
    save()
    return papers,profiles,analysis,parse_data


async def deliver_research(owner,ctx,direction,papers,profiles,analysis,parse_data,selected_ids,message_id):
    from src.agents.monitor_agent import MonitorAgent
    from src.analysis.reader_report import write_reader_report,report_summary
    run=await owner._create_agent_run(ctx.session_id,'MonitorAgent',{},'检查资料获取情况',label='检查资料')
    monitored=await MonitorAgent(owner.db).run(ctx,{'papers':papers})
    missing=monitored.data.get('papers',[])
    await owner._finish_agent_run(run,'MonitorAgent',ctx.session_id,monitored)
    run=await owner._create_agent_run(ctx.session_id,'ReportGenerator',{},'直接回答当前问题并检查报告',label='撰写报告')
    root=ctx.workspace_root/'.agent_history/research'
    snapshot=root/(run+'.json')
    def save():
        atomic_write(snapshot,json.dumps({'direction':direction,'papers':papers,'innovations':profiles,'analysis':analysis},ensure_ascii=False,indent=2))
    save()
    try:
        analysis['reader_report']=await write_reader_report(owner.llm,direction,papers,profiles,analysis)
    finally:
        save()
    path=await owner._generate_report(ctx,direction,papers,profiles,analysis,missing,selected_paper_ids=selected_ids)
    text=path.read_text(encoding='utf-8')
    relative=path.relative_to(ctx.workspace_root).as_posix()
    conclusion=report_summary(text)
    plan=plan_for(direction)
    unfinished=sum(p['status'] in {'not_analyzed','extraction_failed','unassessed'} for p in analysis['paper_processing'])
    meta={'kind':'report','mode':'analysis','task_type':plan['task_type'],
          'answer_status':analysis.get('answer_status'),'report_path':relative,'report_summary':conclusion,
          'reading_coverage':analysis.get('reading_coverage'),
          'partial_report':bool(analysis.get('reader_report_review',{}).get('fallback_delivered')),
          'papers_found':len(papers),'innovations_extracted':sum(not p.get('error') for p in profiles),
          'missing_count':len(missing),'gaps_count':len(analysis.get('gaps',[])),
          'provisional_gaps_count':len(analysis.get('provisional_gaps',[])),
          'benchmark_verified_count':sum(r.get('eligible',False) for r in analysis.get('benchmark_table',[])),
          'answer_count':len(analysis.get('answers',[])),'unprocessed_count':unfinished,'time_scope':plan['time_scope']}
    owner._save_gap_analysis(ctx.session_id,direction,papers,profiles,analysis,owner._collect_evidence(profiles),path,selected_paper_ids=selected_ids)
    assistant_id=str(uuid.uuid4())
    owner.db.execute('INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES(?,?,?,?,?)',
                     (assistant_id,ctx.session_id,'assistant',conclusion,json.dumps(meta,ensure_ascii=False)))
    owner.db.execute("UPDATE sessions SET status='idle', updated_at=datetime('now') WHERE id=?",(ctx.session_id,))
    await owner._finish_agent_run(run,'ReportGenerator',ctx.session_id,AgentResult(status='completed',data={'path':relative}))
    return AgentResult(status='completed',data={**meta,'report_path':str(path),'report_path_rel':relative,
        'user_message_id':message_id,'assistant_message_id':assistant_id,'paper_processing':analysis['paper_processing'],
        'missing_papers':missing[:50],'evidence':owner._collect_evidence(profiles)[:50],'report_content':text})
