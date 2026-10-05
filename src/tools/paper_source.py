"""Look up public source metadata for an existing paper, without model calls."""
from datetime import date
from html.parser import HTMLParser
import re
from urllib.parse import quote
import httpx
from src.analysis.research_plan import paper_arxiv_id, normalize_publication_metadata


class CitationPage(HTMLParser):
    def __init__(self):
        super().__init__();self.meta={};self.text=[]
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='meta' and values.get('name','').startswith('citation_'):
            self.meta[values['name']]=values.get('content','')
    def handle_data(self,data):self.text.append(data)


def arxiv_page_metadata(html,arxiv_id):
    page=CitationPage();page.feed(html)
    text=' '.join(page.text)
    match=re.search(r'Submitted on\s+(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})',text)
    if not match:
        match=re.search(r'\[v1\]\s*(?:[A-Z][a-z]{2},\s*)?(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})',text)
    months='Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
    published=None
    if match and match.group(2) in months:
        published=date(int(match.group(3)),months.index(match.group(2))+1,int(match.group(1))).isoformat()
    return {'arxiv_id':arxiv_id,'title':page.meta.get('citation_title'),
        'publication_date':published,'year':int(published[:4]) if published else None,
        'date_source':'arxiv_first_submission' if published else None,
        'abstract':page.meta.get('citation_abstract'),
        'open_access_pdf_url':f'https://arxiv.org/pdf/{arxiv_id}',
        'source_url':f'https://arxiv.org/abs/{arxiv_id}'}


async def lookup_paper_source(paper):
    normalized=normalize_publication_metadata(paper)
    updates={k:normalized[k] for k in ('publication_date','date_source','year') if normalized.get(k) and normalized.get(k)!=paper.get(k)}
    log=[]
    arxiv_id=paper_arxiv_id(paper)
    async with httpx.AsyncClient(timeout=30,follow_redirects=True) as client:
        if arxiv_id:
            try:
                response=await client.get('https://arxiv.org/abs/'+arxiv_id)
                response.raise_for_status()
                if len(response.content)>5_000_000:raise ValueError('Metadata page is too large')
                parsed=arxiv_page_metadata(response.text,arxiv_id)
                updates.update({k:v for k,v in parsed.items() if v})
                log.append({'source':'arxiv_page','status':'ok','date_found':bool(parsed['publication_date'])})
            except (httpx.HTTPError,ValueError) as exc:
                log.append({'source':'arxiv_page','status':'failed','error':type(exc).__name__})
        doi=paper.get('doi') or (str(paper.get('id'))[4:] if str(paper.get('id')).startswith('doi:') else '')
        if doi:
            try:
                response=await client.get('https://api.crossref.org/works/'+quote(doi,safe=''))
                response.raise_for_status();item=response.json()['message']
                if not updates.get('publication_date'):
                    parts=(item.get('published',{}).get('date-parts') or [[]])[0]
                    if parts:
                        updates.update(publication_date='-'.join(f'{v:04d}' if i==0 else f'{v:02d}' for i,v in enumerate(parts)),
                            year=parts[0],date_source='crossref_published')
                if not updates.get('open_access_pdf_url'):
                    pdf=next((p.get('URL') for p in item.get('link',[]) if p.get('content-type')=='application/pdf'),None)
                    if pdf:updates['open_access_pdf_url']=pdf
                abstract=re.sub('<[^>]+>',' ',item.get('abstract') or '').strip()
                if abstract and not paper.get('abstract'):updates['abstract']=abstract
                log.append({'source':'crossref_lookup','status':'ok'})
            except (httpx.HTTPError,ValueError,KeyError) as exc:
                log.append({'source':'crossref_lookup','status':'failed','error':type(exc).__name__})
    return updates,log
