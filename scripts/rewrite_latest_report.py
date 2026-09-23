"""Write a new report from a saved run without changing its historical report."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def saved_material(db, workspace, row):
    from src.analysis.provenance import bind_profile
    from src.parsing.schemas import DirectionParseResult
    log=json.loads(row.get('search_log_json') or '{}')
    direction=DirectionParseResult(**json.loads(row.get('direction') or '{}')).model_dump()
    papers=[];profiles=[];seen=set()
    for ref in log.get('papers', []):
        pid=ref.get('id')
        if not pid or pid in seen:continue
        seen.add(pid)
        paper=dict(db.fetchone('SELECT * FROM papers WHERE id=?',(pid,)) or ref)
        papers.append(paper)
        stored=db.fetchone('SELECT profile_json FROM innovation_profiles WHERE paper_id=?',(pid,))
        if not stored:continue
        profile=json.loads(stored.get('profile_json') or '{}')
        profile.update(paper_id=pid,paper_title=paper.get('title',''),venue=paper.get('venue',''))
        text=paper.get('abstract') or '';source='abstract';profile['has_fulltext']=False
        raw=paper.get('parsed_markdown_path')
        if raw:
            path=Path(raw)
            if not path.is_absolute():path=workspace/path
            path=path.resolve()
            if path.is_relative_to(workspace) and path.is_file():
                text=path.read_text(encoding='utf-8');source=path.relative_to(workspace).as_posix()
                profile['has_fulltext']=True
        # Re-bind against current source; cached verification is never trusted.
        profile.pop('source_type',None)
        profiles.append(bind_profile(profile,text,source))
    return direction,papers,profiles,log


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',required=True,type=Path)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--remap-only',action='store_true',help='Offline: verify source bindings, leave semantic claims unresolved (default).')
    mode.add_argument('--reanalyze',action='store_true',help='Use the configured model to re-audit the saved paper set; may incur API charges.')
    args=parser.parse_args();workspace=args.workspace.resolve()
    from src.app.factory import build_app
    from src.agents.base import AgentContext
    from src.analysis.evidence_grounded import EvidenceGroundedAnalyzer
    from src.analysis.critic import Critic
    from src.analysis.direction_coverage import build_direction_coverage, ground_direction_candidates
    app,config=build_app(workspace)
    try:
        db=app.state.db;orch=app.state.orchestrator
        row=db.fetchone("SELECT * FROM gap_analyses WHERE id NOT LIKE 'checkpoint_%' ORDER BY created_at DESC,rowid DESC LIMIT 1")
        if not row:raise SystemExit('No saved analysis found.')
        direction,papers,profiles,log=saved_material(db,workspace,row)
        profiles=[orch._normalize_profile(p,p['paper_id']) for p in profiles]
        if args.reanalyze:
            analysis=await orch._analyze_gaps(direction,profiles)
            analysis=orch._enforce_analysis_reliability(direction,profiles,analysis)
            critic=await Critic(app.state.llm).critique(analysis,profiles) if analysis.get('gaps') else {}
            analysis=orch._apply_critic_verdicts(analysis,critic,profiles)
        else:
            analyzer=EvidenceGroundedAnalyzer(None)
            claims=analyzer.claims_from_direction(direction)
            compact=[analyzer.compact_profile(p) for p in profiles if not p.get('error')]
            raw={'paper_assessments':[analyzer._deterministic_assessment(p,claims) for p in compact]}
            audit=analyzer._normalize_batch(raw,compact)
            for p in audit:p['audit_mode']='deterministic_fallback'
            matrix,records=build_direction_coverage(direction,claims,audit)
            analysis={'analysis_scope':'research_direction','matrix':matrix,'coverage_records':records,
                      'coverage_audit':audit,'claims_to_verify':claims,'gaps':[],
                      'provisional_gaps':json.loads(row.get('gaps_json') or '[]')+(log.get('provisional_gaps') or []),
                      'coverage_summary':'仅重新定位原文引用；未执行语义审计，旧候选均待复核。'}
            analysis=ground_direction_candidates(analysis,records)
            analysis=orch._enforce_analysis_reliability(direction,profiles,analysis)
        ctx=AgentContext(workspace_id=app.state.workspace_id,session_id=row.get('session_id') or 'rewrite',
                         workspace_root=workspace,config=config)
        path=await orch._generate_report(ctx,direction,papers,profiles,analysis,
                                        [p for p in papers if not p.get('parsed_markdown_path')],
                                        selected_paper_ids=log.get('selected_paper_ids') or [])
        # Only a new report is written; the stored analysis and original report remain unchanged.
        print(path)
    finally:
        await app.state.llm.close()
        app.state.vs.close();app.state.db.close()


if __name__=='__main__':asyncio.run(main())
