import asyncio, logging, os
from pydantic import BaseModel, Field
import httpx

logger = logging.getLogger(__name__)


class PaperMetadata(BaseModel):
    id: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
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


async def search_semantic_scholar(query: str, limit: int = 20, *, strict=False) -> list[PaperMetadata]:
    headers = {}
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    if key:
        headers["x-api-key"] = key

    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": "title,authors,year,venue,abstract,externalIds,url,openAccessPdf,citationCount",
    }
    results = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params, headers=headers)
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


async def search_arxiv(query: str, limit: int = 20, *, strict=False) -> list[PaperMetadata]:
    import xml.etree.ElementTree as ET
    results = []
    try:
        async with httpx.AsyncClient(timeout=35, follow_redirects=True) as client:
            response = await client.get('https://export.arxiv.org/api/query', params={
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


async def search_crossref(query, limit=10):
    """Public bibliographic fallback. Metadata is never labelled as full text."""
    import re
    async with httpx.AsyncClient(timeout=30,follow_redirects=True) as client:
        response=await client.get('https://api.crossref.org/works',params={
            'query.bibliographic':query,'rows':min(limit,20)},
            headers={'User-Agent':'PaperPilot/0.1 (research desktop)'})
        response.raise_for_status()
    result=[]
    for item in response.json().get('message',{}).get('items',[]):
        doi=item.get('DOI');titles=item.get('title') or []
        if not doi or not titles:continue
        dates=item.get('published',{}).get('date-parts') or [[]]
        # Crossref links may be licensed TDM endpoints; let the downloader check access.
        pdf=next((p.get('URL') for p in item.get('link',[]) if p.get('content-type')=='application/pdf'),None)
        result.append(PaperMetadata(id='doi:'+doi,doi=doi,title=titles[0],
            year=dates[0][0] if dates[0] else None,abstract=re.sub('<[^>]+>',' ',item.get('abstract') or '').strip(),
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
    for item in results:
        p = _as_metadata(item)
        if not p:
            continue
        key = p.doi or p.arxiv_id or p.semantic_scholar_id or p.id
        if key and key in seen_ids:
            continue
        if key:
            seen_ids.add(key)
        norm_title = " ".join(p.title.lower().split())[:80]
        if norm_title in seen_titles:
            continue
        seen_titles.add(norm_title)
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
                     enable_arxiv: bool = True) -> list[PaperMetadata]:
    tasks = []
    names = []
    if enable_mcp and mcp_servers:
        tasks.append(search_mcp(query, mcp_servers, limit))
        names.append('mcp')
    if enable_semantic_scholar:
        tasks.append(search_semantic_scholar(query, limit, strict=True))
        names.append('semantic_scholar')
    if enable_arxiv:
        tasks.append(search_arxiv(query, limit, strict=True))
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
            fallback=await search_crossref(query,limit)
            all_results.extend(fallback)
            diagnostics.append({'source':'crossref','status':'ok','returned_records':len(fallback),'fallback':True})
        except Exception as exc:
            diagnostics.append({'source':'crossref','status':'failed','error_type':type(exc).__name__,
                                'http_status':getattr(getattr(exc,'response',None),'status_code',None),'fallback':True})
    # Rate limit: add a small delay between successive S2 calls
    await asyncio.sleep(0.5)
    class SearchResults(list):
        pass
    merged=SearchResults(await merge_and_deduplicate(all_results))
    merged.diagnostics=diagnostics
    return merged
