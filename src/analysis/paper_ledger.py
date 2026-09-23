"""Account for every input, including papers that never reached evidence audit."""
def paper_ledger(papers, profiles, audit, parse_data=None):
    profiles={p.get('paper_id'):p for p in profiles}
    audited={p.get('paper_id'):p for p in audit if isinstance(p,dict)}
    parse_data=parse_data or {}
    deferred=set(parse_data.get('deferred',[]))
    failures={p.get('paper_id') for p in parse_data.get('failed',[])}
    rows=[]
    for paper in papers:
        pid=paper.get('id')
        profile=profiles.get(pid)
        assessment=audited.get(pid)
        from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
        kind=EvidenceGroundedAnalyzer._source_type(profile or {'paper_id':pid, **paper})
        if kind in {'prior_knowledge','survey_or_repository'}:
            state='excluded';reason='背景资料，不作为论文全文或直接覆盖证据'
        elif not profile:
            state='not_analyzed'
            reason='达到本轮全文处理上限' if pid in deferred else '全文解析失败' if pid in failures else '尚未提取研究画像'
        elif profile.get('error'):
            state='extraction_failed';reason='画像提取失败：'+str(profile.get('error'))[:600]
        elif not assessment or assessment.get('assessment_status')!='audited':
            state='unassessed';reason='已提取画像，跨论文证据审计未完成'
        elif assessment.get('audit_mode')=='deterministic_fallback':
            state='unassessed';reason='语义审计失败，仅完成本地字段映射'
        else:
            state='fulltext' if profile.get('has_fulltext') else 'abstract'
            reason='全文证据画像' if state=='fulltext' else '仅摘要证据'
        rows.append({'paper_id':pid,'title':paper.get('title') or pid,'status':state,'reason':reason})
    return rows
