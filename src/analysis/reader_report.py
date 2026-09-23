"""Write and check a reader-facing deliverable from the completed evidence analysis."""
import json
import re
from collections import Counter
from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer

REPORT_HEADINGS = ('结论', '候选研究空白', '已有工作', '本轮边界')


def report_summary(text):
    match=re.search(r'^## 结论\s*\n(.*?)(?=^## |\Z)',text,re.M|re.S)
    if not match: return ''
    paragraph=match.group(1).strip().split('\n\n',1)[0]
    if len(paragraph)<=1200: return paragraph
    end=max(paragraph.rfind('。',0,1200),paragraph.rfind('；',0,1200))
    return paragraph[:end+1] if end>=0 else paragraph[:1200]+'…'


def report_sources(papers, profiles):
    by_id = {p.get('paper_id'):p for p in profiles if not p.get('error')}
    result=[]
    for p in papers:
        profile=by_id.get(p['id'])
        if not profile:
            continue
        compact=EvidenceGroundedAnalyzer.compact_profile(profile)
        if compact['source_type'] not in {'paper_fulltext','paper_abstract'}:
            continue
        compact['evidence']=[e for e in compact['evidence'] if e.get('quote') and e.get('source_span')]
        if not compact['evidence']:
            continue
        result.append({'number':len(result)+1,'title':p.get('title') or p['id'],
                       'url':p.get('url') or '', **compact})
    return result


def validate_report(text, sources):
    errors=[]
    if not isinstance(text,str) or len(text.strip())<300:
        return ['报告正文为空或不足以回答研究问题']
    if re.search(r'<think|```json|coverage_summary|claim_\d+|evidence_ids|paper_id',text,re.I):
        errors.append('正文混入内部数据或思考标记')
    citations = {int(n) for n in re.findall(r'\[(\d+)\]',text)}
    if citations - {s['number'] for s in sources}:
        errors.append('正文引用了本轮不存在的文献编号')
    if sources and not citations:
        errors.append('没有文献引用')
    headings=re.findall(r'^##[ \t]+([^\r\n]+)',text,re.M)
    positions=[]
    for heading in REPORT_HEADINGS:
        if heading not in headings:
            errors.append('缺少## '+heading)
        else:
            positions.append(headings.index(heading))
    if len(positions)==len(REPORT_HEADINGS) and positions!=sorted(positions):
        errors.append('章节顺序应为：结论、候选研究空白、已有工作、本轮边界')
    return errors


def report_execution_summary(direction, papers, analysis):
    """Keep completed input processing separate from search results not admitted."""
    search=direction.get('search_execution') or {}
    completed=search.get('completed_queries') or []
    done={item.get('query') for item in completed if isinstance(item,dict) and item.get('query')}
    pending=list(dict.fromkeys(q for q in search.get('unexecuted_queries',[]) if q not in done))
    ledger={item['paper_id']:item for item in analysis.get('paper_processing',[]) if item.get('paper_id')}
    ids={p['id'] for p in papers}
    states=Counter(ledger.get(pid,{}).get('status','not_analyzed') for pid in ids)
    return {'included_records':len(ids),'included_processing':dict(states),
            'executed_queries':len(done),'unexecuted_queries':pending,
            'query_diagnostics':completed,
            'new_records_admitted':search.get('new_papers',0),
            'search_records_not_admitted':search.get('deferred_new_records',0),
            'search_stop_reason':search.get('stop_reason'),
            'counting_note':'额外检索但未纳入的记录不属于 included_records；历史补查计数不是当前处理状态。已执行查询不等于接口成功，接口结果见诊断。'}


async def write_reader_report(llm, direction, papers, profiles, analysis):
    sources=report_sources(papers,profiles)
    payload={'question':direction.get('user_request') or direction.get('research_question'),
             'research_scope':direction.get('research_question'),
             'sources':sources,
             'candidates':{k:analysis.get(k,[]) for k in ('gaps','provisional_gaps','rejected_gaps')},
             'coverage':analysis.get('coverage_summary'),
             'review':analysis.get('critic',{}),
             'completed_followup':analysis.get('followup_actions',[]),
             'execution_summary':report_execution_summary(direction,papers,analysis),
             'paper_processing':analysis.get('paper_processing',[])}
    system = '''你是论文研究Agent的报告作者，直接回答这个方向有哪些值得研究的具体空白。输出中文Markdown正文，约1500—3000字；资料少时不凑字数。
依次按 # 简短题目、## 结论、## 候选研究空白、## 已有工作、## 本轮边界 组织，不再使用“研究判断”作为章节名。
结论先用一个不超过120字的短段点名最值得关注的候选空白及优先项；一句话说明这些判断基于本轮资料。不要以长篇已有工作回顾或反驳用户没有说过的观点开头。
候选研究空白是正文重点，放在已有工作之前。每项用“### 1. 具体空白名称”这样的标题直接点出尚待解决的问题或缺少的比较，不要只写方法名称或“某某研究判断”。证据较弱时在标题末标“（待核实）”。
每项开头写“**空白点：**”并用一两句直接说明在什么条件下缺少哪种方法、能力、证据或受控比较。随后写“**与已有工作的差异：**”，说明最近工作做到了哪一步、该候选还要解决什么，紧跟文献引用。必要时最后用一句“可以研究：”说明具体研究切入点。
每项2—3个短段，每段不超过180字。优先写证据支持且符合用户范围的候选，通常3—4项；只有1项就写1项，不为凑数制造空白。不要堆输入字段或反复解释内部评审状态。
已有工作用简短对照表：论文、已做什么、与候选空白的关系。把详细方法回顾放在这里。
背景笔记、题录和无有效原文引用的记录不在sources中，不能据其标题推断机制；必要时在边界说明中直接点名缺失资料。
正式候选为空时仍可列出有具体依据的待核实候选；如果连这样的候选也没有，就在“候选研究空白”下直接说明“本轮未识别出有依据的候选空白”及具体原因，不强行编造。
被排除的 rejected 候选只放在已有工作或边界中解释，不混进候选研究空白。provisional 候选标注待核实，不得把文献未找到说成全球不存在。
supported_candidate 和 narrow_candidate 也只是当前资料内的研究假设，不代表空白已被证明。
若本轮仍有直接相关论文未阅读或未完成核查，受影响方向只能写为待核实假设，不能一边称空白成立一边承认潜在反例未读。
“整图统一权重”“没有某模块”等否定性事实需要方法全文直接支持，不能由提取片段没有提到推断。
读者不需要看到你的思考、流程口令、JSON字段、ID、置信度小数或内部审计条款。
用 [1] 这样的输入文献编号紧跟事实判断。只能引用 sources 中的资料，不能捏造论文、结论、性能和实验结果。
结论与解释用完整短句。“空白点”和“与已有工作的差异”是给读者的重点提示，不要继续堆“待补证据、支持指标、停止条件”等字段清单。
不要写“推荐下一步”并把搜索、下载、阅读和比较推给用户；本轮已经自动做的核查要说明结果。
确实被权限、网络、全文获取或预算阻断的事项写在本轮边界中，解释会影响哪项判断。
所有处理数量与检索数量以 execution_summary 为准，不引用历史补查记录里的中间计数作为当前结果。分别写已执行查询和未执行查询，不把已执行数量误称总计划数量；检索但未纳入的额外记录不属于本轮已纳入资料，不得混称尚未处理的论文。
训练实验尚未执行，可以在具体研究假设后说明怎样检验，但不能声称Agent已完成训练。
用户问“还有哪些空白”是开放问题，不等于声称“没有已有工作”；直接回答问题，不要捏造一个断言再反驳。尊重用户明确给出的条件，不自行换成另一种研究设定。
区分直接同范围工作与相邻工作：来源、目标、训练条件不同的研究可以启发方法，但要简短说明差别，不能混称直接覆盖。以上是写作约束，不要求证明不存在任何遗漏；不确定处简短标注，不用反复免责声明淹没空白点。
不要输出参考文献章节，由程序统一追加。'''
    messages=[{'role':'system','content':system},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
    for attempt in range(2):
        llm.presentation='report_draft'
        try:
            text=await llm.chat(messages, model=llm.reasoning_model, purpose='analysis')
        finally:
            llm.presentation='research'
        analysis['reader_report_draft']=text
        analysis['reader_report_review']={'passed':False,'attempts':attempt+1,'issues':['正文已生成，交付检查尚未完成']}
        errors=validate_report(text,sources)
        review=await llm.chat_json([
            {'role':'system','content':
             '检查报告是否忠实回答问题并受给定证据约束。只输出JSON {"progress_summary":"检查发现的简短说明","pass":true/false,"issues":[具体问题]}。'
             '这是基于本轮资料的研究建议，不要求证明全球不存在相关工作。明确标注待核实、有具体依据和最近工作差异的候选可以通过；不要仅因候选未被最终证实就拒绝报告。'
             '检查是否按结论、候选研究空白、已有工作、本轮边界排列，并在每个候选开头直接点出空白点与已有工作的差异；不要把空白埋在综述中。'
             '检查有无伪造引用、把假设写成确认空白、颠倒迁移方向、夸大审计状态、替用户编造断言、'
             '把可执行文献任务直接推给用户、内部字段堆砌、缺少直接结论。'
             '统计必须与 execution_summary 一致；已执行查询不等于计划总数，额外检索但未纳入的记录不属于本轮已纳入资料。混用范围应判为不通过。'
             '有直接相关论文未读却宣称空白成立、把摘录未提及写成方法没有该机制，必须判为不通过。'
             '训练实验方案允许，但不能伪称已经完成。'},
            {'role':'user','content':json.dumps({'input':payload,'report':text},ensure_ascii=False)}],
            model=llm.reasoning_model,purpose='analysis')
        if review.get('pass') is not True:
            errors += [str(i) for i in review.get('issues',[])] or ['报告未通过内容检查']
        if not errors:
            refs=['\n## 参考文献\n']
            used={int(n) for n in re.findall(r'\[(\d+)\]',text)}
            for source in sources:
                if source['number'] in used:
                    title=source['title'].replace('\n',' ')
                    url=source['url']
                    label=f'[{title}]({url})' if url.startswith(('https://','http://')) and not any(c in url for c in '\n )') else title
                    refs.append(f"- [{source['number']}] {label}（{'全文' if source['has_fulltext'] else '摘要'}）")
            analysis['reader_report_review']={'passed':True,'attempts':attempt+1}
            return text.rstrip()+'\n'+'\n'.join(refs)+'\n'
        analysis['reader_report_review']={'passed':False,'attempts':attempt+1,'issues':errors}
        analysis['reader_report_draft']=text
        messages += [{'role':'assistant','content':text},{'role':'user','content':'请修正以下检查问题并重新交付完整报告：'+json.dumps(errors,ensure_ascii=False)}]
    raise RuntimeError('报告未通过内容检查，研究资料已保留：'+'；'.join(errors)[:500])
