"""Full-text access links and research-scoped missing papers, without model calls."""
import re
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from src.knowledge.library_organizer import decode


def safe_web_url(value):
    value=str(value or '').strip()
    if any(ord(c)<32 for c in value):return ''
    try:
        parts=urlsplit(value)
        if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password:return ''
        return value
    except ValueError:return ''


def source_links(paper, sources=()):
    links=[];seen=set()
    def add(label,url,kind):
        url=safe_web_url(url)
        if url and url not in seen:
            seen.add(url);links.append({'label':label,'url':url,'kind':kind})
    for item in (paper,*sources):
        doi=str(item.get('doi') or '')
        if not doi and str(item.get('id','')).startswith('doi:'):doi=item['id'][4:]
        doi=re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)','',doi.strip(),flags=re.I)
        if re.fullmatch(r'10\.\d+/.+',doi):add('出版社 / DOI 原文页','https://doi.org/'+quote(doi,safe='/'),'source')
        arxiv=str(item.get('arxiv_id') or '')
        if not arxiv and str(item.get('id','')).startswith('arxiv:'):arxiv=item['id'][6:]
        if not arxiv:
            match=re.search(r'arxiv\.org/(?:abs|pdf)/([^?#]+)',str(item.get('url') or ''),re.I)
            if match:arxiv=match[1]
        arxiv=re.sub(r'^(?:arxiv:|https?://arxiv\.org/(?:abs|pdf)/)','',arxiv,flags=re.I)
        arxiv=re.sub(r'\.pdf$','',arxiv,flags=re.I)
        if re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})(?:v\d+)?',arxiv,re.I):
            add('arXiv 论文页','https://arxiv.org/abs/'+arxiv,'source')
        add('已记录的 PDF 地址',item.get('open_access_pdf_url'),'pdf_candidate')
        url=safe_web_url(item.get('url'))
        if url:
            host=urlsplit(url).hostname
            label='会议原文页' if host=='openaccess.thecvf.com' else '已有来源页'
            add(label,url,'source')
    title=str(paper.get('title') or '').strip()
    if title:
        add('Google Scholar','https://scholar.google.com/scholar?'+urlencode({'q':'"'+title+'"'}),'search')
        add('Semantic Scholar','https://www.semanticscholar.org/search?'+urlencode({'q':title,'sort':'relevance'}),'search')
        add('arXiv 题名检索','https://arxiv.org/search/?'+urlencode({'query':title,'searchtype':'title','abstracts':'show','order':'-announced_date_first','size':50}),'search')
        add('网页题名检索','https://www.google.com/search?'+urlencode({'q':'"'+title+'" PDF'}),'search')
    return links


def local_fulltext_exists(workspace,paper):
    root=Path(workspace).resolve()
    for field in ('fulltext_path','parsed_markdown_path'):
        value=paper.get(field)
        if not value:continue
        try:
            path=(root/value).resolve();path.relative_to(root)
            if path.is_file() and path.stat().st_size>0:return True
        except (OSError,ValueError):pass
    return False


def research_usage(db):
    """Only latest completed analysis per existing session; checkpoints aren't results."""
    latest={};usage={}
    rows=db.fetchall('SELECT g.* FROM gap_analyses g JOIN sessions s ON s.id=g.session_id WHERE g.id NOT LIKE ? ORDER BY g.created_at DESC,g.rowid DESC',('checkpoint_%',))
    for row in rows:
        sid=row['session_id']
        if sid in latest:continue
        latest[sid]=row
        log=decode(row.get('search_log_json'),{})
        if not isinstance(log,dict):continue
        ids={p['id'] for p in log.get('papers',[]) if isinstance(p,dict) and p.get('id')}
        selected={p for p in log.get('selected_paper_ids',[]) if isinstance(p,str)}
        evidence=decode(row.get('evidence_json'),[])
        evidenced={e['paper_id'] for e in evidence if isinstance(e,dict) and e.get('paper_id')} if isinstance(evidence,list) else set()
        for pid in ids|selected|evidenced:
            usage.setdefault(pid,[]).append({'session_id':sid,'selected':pid in selected,'evidenced':pid in evidenced})
    return usage


def missing_papers(db,workspace,scope='research',session_id='',q='',limit=100,offset=0):
    rows=db.fetchall("SELECT p.*,o.canonical_id,o.category FROM papers p LEFT JOIN paper_organization o ON o.paper_id=p.id WHERE p.id NOT LIKE 'prior:%' AND COALESCE(p.retrieval_status,'')!='off_topic' ORDER BY p.updated_at DESC,p.id")
    groups={};by_id={r['id']:r for r in rows}
    for row in rows:groups.setdefault(row.get('canonical_id') or row['id'],[]).append(row)
    usage=research_usage(db);items=[];raw_missing=0
    for pid,members in groups.items():
        if any(local_fulltext_exists(workspace,p) for p in members):continue
        raw_missing+=len(members)
        paper=dict(by_id.get(pid,members[0]));paper['canonical_id']=pid
        contexts=[u for member in members for u in usage.get(member['id'],[])]
        current=[u for u in contexts if not session_id or u['session_id']==session_id]
        relevant=current if scope=='session' else contexts
        priority='selected' if any(u['selected'] for u in relevant) else 'evidence' if any(u['evidenced'] for u in relevant) else 'included' if relevant else 'library'
        reason=str(paper.get('missing_reason') or '')
        if reason=='user_not_found':access_status='已记录：你也未找到全文'
        elif reason.startswith('awesome_'):access_status='仅导入了题录，未记录全文获取尝试'
        elif paper.get('fulltext_path') or paper.get('parsed_markdown_path'):access_status='曾记录全文路径，但当前本地文件不可用'
        elif paper.get('retrieval_status')=='parse_failed':access_status='此前获取或解析未成功，当前没有可用本地全文'
        elif reason in {'no_pdf_available','no_open_fulltext'}:access_status='此前未自动获取全文，可打开来源页查找'
        elif reason and not re.fullmatch(r'[A-Za-z0-9_-]+',reason):access_status=reason
        else:access_status='当前仅有题录或摘要，未取得本地全文'
        paper.update(source_links=source_links(paper,members),source_ids=[p['id'] for p in members],
                     duplicate_count=len(members)-1,used_in_research=bool(contexts),
                     in_current_session=bool(current) if session_id else False,
                     usage_priority=priority,access_status=access_status,
                     usage_label={'selected':'研究中曾由你选入','evidence':'研究中有摘要或证据引用','included':'研究中曾纳入资料','library':'仅在论文库中，未纳入现有研究最近一轮'}[priority])
        items.append(paper)
    used=sum(p['used_in_research'] for p in items)
    summary={'missing_total':len(items),'research_total':used,'library_only_total':len(items)-used,
             'raw_missing_records':raw_missing,'folded_records':raw_missing-len(items)}
    filtered=[p for p in items if (scope=='workspace' or (scope=='session' and p['in_current_session']) or (scope=='research' and p['used_in_research']))]
    if q.strip():
        term=q.strip().casefold()
        filtered=[p for p in filtered if term in ' '.join(str(p.get(k) or '') for k in ('id','title','venue','doi','arxiv_id','category')).casefold()]
    order={'selected':0,'evidence':1,'included':2,'library':3}
    filtered.sort(key=lambda p:order[p['usage_priority']])
    return {'papers':filtered[offset:offset+limit],'scope':scope,'total':len(filtered),'count':min(limit,max(0,len(filtered)-offset)),
            'offset':offset,'limit':limit,'summary':summary}
