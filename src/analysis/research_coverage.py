"""Reading coverage and bounded primary-source follow-up for benchmark leads."""
import re
from pathlib import Path


def local_fulltext(paper, workspace):
    root=Path(workspace).resolve()
    for key in ('parsed_markdown_path','fulltext_path','local_pdf_path'):
        if not paper.get(key):continue
        path=Path(paper[key])
        if not path.is_absolute():path=root/path
        if path.resolve().is_relative_to(root) and path.is_file():return True
    return False


def pending_reading(papers, profiles, selected_ids, attempted, workspace):
    selected=set(selected_ids)
    successful={p['paper_id']:p for p in profiles if not p.get('error')}
    pending=[]
    for paper in papers:
        pid=paper['id'];local=local_fulltext(paper,workspace)
        required=pid in selected or local or paper.get('benchmark_followup')
        profile=successful.get(pid)
        complete=profile and (not local or profile.get('has_fulltext') or profile.get('source_type')=='paper_fulltext')
        if required and not complete and attempted.get(pid,0)<2:pending.append(paper)
    pending.sort(key=lambda p:(p['id'] not in selected,not bool(p.get('benchmark_followup')),
                               not local_fulltext(p,workspace),-float(p.get('relevance_score') or 0)))
    return pending


def reading_coverage(papers, profiles, selected_ids, attempted, workspace, read_limit):
    successful={p['paper_id'] for p in profiles if not p.get('error')}
    admitted={p['id'] for p in papers}
    unread=[p['id'] for p in papers if p['id'] not in successful]
    pending=pending_reading(papers,profiles,selected_ids,attempted,workspace)
    fulltext_shortfall=[p['id'] for p in papers if local_fulltext(p,workspace) and not any(
        profile['paper_id']==p['id'] and not profile.get('error') and
        (profile.get('has_fulltext') or profile.get('source_type')=='paper_fulltext') for profile in profiles)]
    excerpt_only=[p['paper_id'] for p in profiles if p.get('reading_input',{}).get('input_complete') is False and p['paper_id'] in admitted]
    return {'read_limit':read_limit,'admitted':len(admitted),'completed':len(successful & admitted),
            'unread_ids':unread,'selected_not_admitted':sorted(set(selected_ids)-admitted),
            'pending_required_ids':[p['id'] for p in pending],'fulltext_shortfall_ids':fulltext_shortfall,
            'excerpt_only_ids':excerpt_only,
            'attempted':len(attempted),'status':'partial' if unread or fulltext_shortfall or excerpt_only or set(selected_ids)-admitted else 'complete'}


def model_name(value):
    return re.sub(r'\[\s*\d+(?:\s*,\s*\d+)*\s*\]','',str(value)).strip()


def model_key(value):
    return re.sub(r'[^\w+]','',model_name(value).casefold())


def matches_model(title, model):
    # Exact leading method name prevents e.g. Fast-Example from matching Example.
    return model_key(re.split(r'[:：]',str(title),maxsplit=1)[0])==model_key(model)


def baseline_leads(rows, profiles, plan):
    lower=(plan.get('comparison') or {}).get('lower_is_better',True)
    resolved=[p.get('paper_title','') for p in profiles if not p.get('error')]
    leads={}
    for row in rows:
        if row.get('row_role')!='baseline' or not row.get('source_bound'):continue
        model=model_name(row['model'])
        if not model or any(matches_model(title,model) for title in resolved):continue
        peers=[r['value'] for r in rows if r.get('eligible') and r.get('group')==row.get('group') and
               r.get('dataset')==row.get('dataset') and r.get('metric')==row.get('metric') and
               r.get('protocol','')==row.get('protocol','')]
        if peers and not (row['value']<min(peers) if lower else row['value']>max(peers)):continue
        key=model_key(model);old=leads.get(key)
        if old is None or (row['value']<old['value'] if lower else row['value']>old['value']):
            leads[key]={'model':model,'value':row['value'],'group':row.get('group'),
                        'dataset':row.get('dataset'),'metric':row.get('metric'),'source_paper_id':row['paper_id']}
    return sorted(leads.values(),key=lambda row:row['value'],reverse=not lower)[:6]


def local_baseline_papers(owner, model):
    pattern=model.replace('!','!!').replace('%','!%').replace('_','!_')+'%'
    rows=owner.db.fetchall("SELECT id,title FROM papers WHERE title LIKE ? ESCAPE '!' LIMIT 20",(pattern,))
    return owner._selected_papers([row['id'] for row in rows if matches_model(row['title'],model)])
