import asyncio, logging, os
from pydantic import BaseModel, Field
import httpx

logger = logging.getLogger(__name__)


async def metadata_request(client, url, **options):
    response=await client.get(url,**options)
    if response.status_code==429:
        try:delay=float(response.headers.get('Retry-After','3'))
        except ValueError:delay=3
        if delay<=12:
            await asyncio.sleep(max(3,delay))
            response=await client.get(url,**options)
    return response


class PaperMetadata(BaseModel):
    id: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    publication_date: str | None = None
    date_source: str | None = None
    temporal_status: str | None = None
    temporal_reason: str | None = None
    venue: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    semantic_scholar_id: str | None = None
    url: str | None = None
    open_access_pdf_url: str | None = None
    citation_count: int = 0
    source: str = ""
    retrieval_status: str | None = None
    missing_reason: str | None = None
    fulltext_path: str | None = None
    local_pdf_path: str | None = None
    parsed_markdown_path: str | None = None


async def search_semantic_scholar(query: str, limit: int = 20, *, strict=False, time_scope=None) -> list[PaperMetadata]:
    headers = {}
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    if key:
        headers["x-api-key"] = key

    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": "title,authors,year,publicationDate,venue,abstract,externalIds,url,openAccessPdf,citationCount",
    }
    if time_scope:
        # S2 maps unknown dates to January 1. Include the boundary year and filter locally.
        params['publicationDateOrYear'] = time_scope['start'][:4]+':'+time_scope['end']
    results = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await metadata_request(client,url,params=params,headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                for p in data.get("data", []):
                    ext = p.get("externalIds", {}) or {}
                    oa = p.get("openAccessPdf", {}) or {}
                    corpus_id = ext.get("CorpusId", "")
                    paper_id_val = p.get("paperId") or ext.get("paperId", "")
                    results.append(PaperMetadata(
                        id=f"s2:{paper_id_val or corpus_id}",
                        title=p.get("title", ""),
                        authors=[a.get("name", "") for a in (p.get("authors", []) or [])],
                        year=p.get("year"),
                        publication_date=p.get('publicationDate'), date_source='semantic_scholar',
                        venue=p.get("venue"),
                        abstract=p.get("abstract"),
                        doi=ext.get("DOI"),
                        arxiv_id=ext.get("ArXiv"),
                        semantic_scholar_id=str(paper_id_val or corpus_id),
                        url=p.get("url"),
                        open_access_pdf_url=oa.get("url"),
                        citation_count=p.get("citationCount", 0),
                        source="semantic_scholar",
                    ))
            else:
                if strict: resp.raise_for_status()
                logger.warning(f"Semantic Scholar returned {resp.status_code}")
    except Exception as e:
        if strict: raise
        logger.warning(f"Semantic Scholar search failed: {e}")
    return results


async def search_arxiv(query: str, limit: int = 20, *, strict=False, time_scope=None) -> list[PaperMetadata]:
    import xml.etree.ElementTree as ET
    results = []
    if time_scope:
        start=time_scope['start'].replace('-','')+'0000'
        end=time_scope['end'].replace('-','')+'2359'
        query=f'({query}) AND submittedDate:[{start} TO {end}]'
    try:
        async with httpx.AsyncClient(timeout=35, follow_redirects=True) as client:
            response = await metadata_request(client,'https://export.arxiv.org/api/query', params={
                'search_query':query, 'start':0, 'max_results':min(limit,50), 'sortBy':'relevance'},
                headers={'User-Agent':'PaperPilot/0.1 (research desktop)'})
            response.raise_for_status()
        namespace={'a':'http://www.w3.org/2005/Atom'}
        for entry in ET.fromstring(response.content).findall('a:entry',namespace):
            def value(key):
                return ' '.join((entry.findtext('a:'+key,default='',namespaces=namespace)).split())
            url=value('id')
            if '/abs/' not in url:continue
            arxiv_id=url.split('/abs/')[-1]
            published=value('published')
            results.append(PaperMetadata(
                id=f"arxiv:{arxiv_id}",
                title=value('title'),
                authors=[a.findtext('a:name',default='',namespaces=namespace) for a in entry.findall('a:author',namespace)],
                year=int(published[:4]) if published[:4].isdigit() else None,
                publication_date=published[:10] or None, date_source='arxiv_published',
                abstract=value('summary'),
                arxiv_id=arxiv_id,
                url=url.replace('http://','https://'),
                open_access_pdf_url='https://arxiv.org/pdf/'+arxiv_id,
                source="arxiv",
            ))
    except Exception as e:
        if strict: raise
        logger.warning(f"arXiv search failed: {e}")
    return results


async def search_crossref(query, limit=10, *, time_scope=None):
    """Public bibliographic fallback. Metadata is never labelled as full text."""
    import re
    params={'query.bibliographic':query,'rows':min(limit,20)}
    if time_scope:
        params['filter']=f"from-pub-date:{time_scope['start']},until-pub-date:{time_scope['end']}"
    async with httpx.AsyncClient(timeout=30,follow_redirects=True) as client:
        response=await metadata_request(client,'https://api.crossref.org/works',params=params,
            headers={'User-Agent':'PaperPilot/0.1 (research desktop)'})
        response.raise_for_status()
    result=[]
    for item in response.json().get('message',{}).get('items',[]):
        doi=item.get('DOI');titles=item.get('title') or []
        if not doi or not titles:continue
        dates=item.get('published',{}).get('date-parts') or [[]]
        parts=dates[0]
        published='-'.join(f'{v:04d}' if i==0 else f'{v:02d}' for i,v in enumerate(parts)) or None
        # Crossref links may be licensed TDM endpoints; let the downloader check access.
        pdf=next((p.get('URL') for p in item.get('link',[]) if p.get('content-type')=='application/pdf'),None)
        result.append(PaperMetadata(id='doi:'+doi,doi=doi,title=titles[0],
            year=parts[0] if parts else None,publication_date=published,date_source='crossref_published',abstract=re.sub('<[^>]+>',' ',item.get('abstract') or '').strip(),
            authors=[' '.join(filter(None,[a.get('given'),a.get('family')])) for a in item.get('author',[])],
            url=item.get('URL'),open_access_pdf_url=pdf,source='crossref'))
    return result


def _as_metadata(item: PaperMetadata | dict) -> PaperMetadata | None:
    if isinstance(item, PaperMetadata):
        return item
    if isinstance(item, dict):
        try:
            return PaperMetadata(**item)
        except Exception as e:
            logger.warning(f"Invalid paper metadata skipped: {e}")
            return None
    return None


async def merge_and_deduplicate(results: list[PaperMetadata | dict]) -> list[PaperMetadata]:
    seen_ids = set()
    seen_titles = set()
    merged = []
    by_key, by_title = {}, {}
    for item in results:
        p = _as_metadata(item)
        if not p:
            continue
        key = p.doi or p.arxiv_id or p.semantic_scholar_id or p.id
        norm_title = " ".join(p.title.lower().split())
        old=by_key.get(key) or by_title.get(norm_title)
        if old:
            # A cached entry must not discard a newly retrieved publication date or abstract.
            for name in PaperMetadata.model_fields:
                if not getattr(old,name) and getattr(p,name):
                    setattr(old,name,getattr(p,name))
            if p.publication_date and len(p.publication_date)==10 and (not old.publication_date or len(old.publication_date)<10):
                old.publication_date,old.date_source=p.publication_date,p.date_source
            continue
        if key:by_key[key]=p
        if norm_title:by_title[norm_title]=p
        merged.append(p)
    return merged


def _metadata_from_mcp(item: dict) -> PaperMetadata | None:
    title = item.get("title") or item.get("name") or ""
    if not title:
        return None
    paper_id = item.get("id") or item.get("paper_id") or item.get("url") or title
    pdf_url = item.get("open_access_pdf_url") or item.get("pdf_url")
    return PaperMetadata(
        id=f"mcp:{paper_id}",
        title=title,
        authors=item.get("authors") or [],
        year=item.get("year"),
        publication_date=item.get('publication_date') or item.get('published'), date_source='mcp',
        venue=item.get("venue"),
        abstract=item.get("abstract") or item.get("summary"),
        doi=item.get("doi"),
        arxiv_id=item.get("arxiv_id"),
        semantic_scholar_id=item.get("semantic_scholar_id"),
        url=item.get("url"),
        open_access_pdf_url=pdf_url,
        citation_count=item.get("citation_count", 0) or 0,
        source="mcp",
    )


async def search_mcp(query: str, servers_config: list[dict],
                     limit: int = 20) -> list[PaperMetadata]:
    if not servers_config:
        return []
    from src.mcp.client import MCPClient
    client = MCPClient(servers_config)
    try:
        await client.connect()
        raw_results = await client.search(query)
        papers = []
        for item in raw_results[:limit]:
            if isinstance(item, dict):
                meta = _metadata_from_mcp(item)
                if meta:
                    papers.append(meta)
        return papers
    except Exception as e:
        logger.warning(f"MCP search failed: {e}")
        return []
    finally:
        await client.stop()


async def search_all(query: str, limit: int = 20,
                     mcp_servers: list[dict] | None = None,
                     enable_mcp: bool = False,
                     enable_semantic_scholar: bool = True,
                     enable_arxiv: bool = True, time_scope: dict | None = None) -> list[PaperMetadata]:
    tasks = []
    names = []
    if enable_mcp and mcp_servers:
        tasks.append(search_mcp(query, mcp_servers, limit))
        names.append('mcp')
    if enable_semantic_scholar:
        tasks.append(search_semantic_scholar(query, limit, strict=True, **({'time_scope':time_scope} if time_scope else {})))
        names.append('semantic_scholar')
    if enable_arxiv:
        tasks.append(search_arxiv(query, limit, strict=True, **({'time_scope':time_scope} if time_scope else {})))
        names.append('arxiv')
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_results = []
    diagnostics = []
    for name, result in zip(names, results):
        if not isinstance(result, Exception):
            all_results.extend(result)
            diagnostics.append({'source':name,'status':'ok','returned_records':len(result)})
        else:
            status=getattr(getattr(result,'response',None),'status_code',None)
            diagnostics.append({'source':name,'status':'failed','error_type':type(result).__name__,'http_status':status})
            logger.warning('Search failed: %s (%s, HTTP %s)',name,type(result).__name__,status)
    if not all_results and (enable_semantic_scholar or enable_arxiv):
        try:
            fallback=await search_crossref(query,limit,**({'time_scope':time_scope} if time_scope else {}))
            all_results.extend(fallback)
            diagnostics.append({'source':'crossref','status':'ok','returned_records':len(fallback),'fallback':True})
        except Exception as exc:
            diagnostics.append({'source':'crossref','status':'failed','error_type':type(exc).__name__,
                                'http_status':getattr(getattr(exc,'response',None),'status_code',None),'fallback':True})
    # Rate limit: add a small delay between successive S2 calls
    await asyncio.sleep(3 if enable_arxiv else 0.5)
    class SearchResults(list):
        pass
    merged=SearchResults(await merge_and_deduplicate(all_results))
    if time_scope:
        from src.analysis.research_plan import scoped_papers
        kept, excluded=scoped_papers([p.model_dump() for p in merged],{'research_plan':{'time_scope':time_scope}})
        merged=SearchResults([PaperMetadata(**p) for p in kept])
        diagnostics.append({'source':'date_filter','status':'ok','excluded_records':len(excluded), 'time_scope':time_scope})
    merged.diagnostics=diagnostics
    return merged
