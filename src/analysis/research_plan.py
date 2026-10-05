"""Question-specific research plans and reproducible publication-date scope."""
from datetime import date, datetime, timezone, timedelta
import calendar
import re
from pydantic import BaseModel, Field, field_validator


class ResearchPlan(BaseModel):
    task_type: str = 'question_answer'
    source_scope: str = 'open_literature'
    objective: str = ''
    extraction_fields: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    report_sections: list[str] = Field(default_factory=list)
    comparison: dict = Field(default_factory=dict)
    comparison_mode: str = 'reported_results'
    time_scope: dict = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    adaptive: bool = True

    @field_validator('comparison_mode',mode='before')
    @classmethod
    def valid_comparison_mode(cls,value):
        return value if isinstance(value,str) and value in {'reported_results','controlled_comparison'} else 'reported_results'


def research_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def resolve_time_scope(message, proposed=None, today=None):
    """Relative periods come from the user's words and the clock, never model memory."""
    today = today or research_today()
    scope = dict(proposed or {})
    text = str(message).lower()
    recent = re.search(r'(?:最近|近|过去|过去的|近来)\s*([一二两三四五六七八九十\d]+)\s*年|(?:last|past)\s+(one|two|three|\d+)\s+years?', text)
    if '一两年' in text or '一到两年' in text or '1-2 years' in text:
        years = 2
        assumption = '“最近一两年”按近两年处理。'
    elif recent:
        token = recent.group(1) or recent.group(2)
        years = {'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'十':10,'one':1,'two':2,'three':3}.get(token)
        years = years if years is not None else int(token) if token.isdigit() else None
        assumption = ''
    elif re.search(r'last year|past year|近一年|最近一年', text):
        years, assumption = 1, ''
    else:
        years, assumption = None, ''
    if years:
        start_year = today.year - min(years, 30)
        start = date(start_year, today.month, min(today.day, calendar.monthrange(start_year, today.month)[1]))
        return {'start':start.isoformat(), 'end':today.isoformat(), 'basis':'publication',
                'resolved_at':today.isoformat(), 'user_expression':recent.group(0) if recent else '最近一两年',
                'assumption':assumption}
    # Calendar ranges are also recovered if a model omits its structured date field.
    match = re.search(r'((?:19|20)\d{2})\s*(?:[-–—至到]|and)\s*((?:19|20)\d{2})', text)
    if match:
        scope.update(start=match.group(1)+'-01-01', end=match.group(2)+'-12-31')
    if scope.get('start') and scope.get('end'):
        try:
            start, end = date.fromisoformat(scope['start']), date.fromisoformat(scope['end'])
            if start <= min(end, today):
                return {**scope, 'start':start.isoformat(), 'end':min(end,today).isoformat(), 'basis':'publication'}
        except (ValueError, TypeError):
            pass
    return {}


def normalize_plan(value, message, today=None):
    raw = value if isinstance(value, dict) else {}
    # Old saved directions remain usable; explicit numerical requests never fall into gap discovery.
    fallback = 'benchmark_comparison' if re.search(r'最低|最高|多少|best|lowest|highest', message, re.I) and re.search(r'EPE|准确率|精度|指标|benchmark|accuracy|latency|延迟',message,re.I) else 'gap_discovery'
    plan = ResearchPlan.model_validate(raw or {'task_type':fallback, 'adaptive':fallback!='gap_discovery'}).model_dump()
    if plan['task_type'] not in {'gap_discovery','benchmark_comparison','literature_review','claim_verification','question_answer'}:
        plan['task_type'] = 'question_answer'
    if plan['source_scope'] not in {'provided','open_literature'}:
        plan['source_scope'] = 'open_literature'
    if plan['comparison_mode'] not in {'reported_results','controlled_comparison'}:
        plan['comparison_mode'] = 'reported_results'
    plan['objective'] = plan['objective'] or message
    plan['time_scope'] = resolve_time_scope(message, plan['time_scope'], today)
    if plan['task_type']=='benchmark_comparison':
        # A recent search window must not silently narrow an explicit all-model comparison.
        broad=r'(?:全部|所有|不限年份的?)\s*[^。；;\n]{0,18}?(?:模型|方法)|(?:all|any)\s+(?:\w+\s+){0,3}(?:models|methods)'
        portions=re.findall(r'[（(]([^（）()]+)[）)]',message)
        portions=[part for part in portions if re.search(broad,part,re.I)] or [message]
        if any(re.search(broad,part,re.I) for part in portions):
            groups=plan['comparison'].get('groups') or []
            matched=[]
            for group in groups:
                aliases=[str(group),*re.split(r'[ ()（）_-]+',str(group))]
                for terms in (('精度','accuracy'),('实时','real-time'),('质量','quality'),('效率','efficiency')):
                    if any(term.casefold() in str(group).casefold() for term in terms):aliases.extend(terms)
                aliases=[a for a in aliases if a.casefold() not in {'模型','方法','组','model','models','methods','oriented','导向'}]
                aliases=[a for a in aliases if a.casefold() not in {'模型','方法','组','model','models','methods','oriented','导向'}]
                if any(len(alias)>1 and alias.casefold() in part.casefold() for part in portions for alias in aliases):
                    matched.append(group)
            plan['comparison']['all_time_groups']=matched or groups or ['*']
        else:
            plan['comparison'].pop('all_time_groups',None)
    if plan['time_scope'].get('assumption'):
        plan['assumptions'].append(plan['time_scope']['assumption'])
    sections = {'gap_discovery':['结论','候选研究空白','已有工作','本轮边界'],
                'benchmark_comparison':['结论','数值对照','口径与依据','本轮边界']}
    default = sections.get(plan['task_type'], ['结论','分析与依据','本轮边界'])
    middle = [s.strip() for s in plan['report_sections'] if isinstance(s,str) and s.strip() and
              len(s)<40 and not re.search(r'[\r\n#]',s) and s not in {'结论','本轮边界','参考文献'}]
    plan['report_sections'] = default if plan['task_type'] in sections else ['结论',*(middle[:4] or ['分析与依据']),'本轮边界']
    return plan


def plan_for(direction):
    return direction.get('research_plan') or normalize_plan({}, direction.get('user_request') or direction.get('research_question') or '')


def controlled_comparison(plan):
    """Legacy plans default to literature comparisons, not controlled experiments."""
    return plan.get('comparison_mode') == 'controlled_comparison'


def paper_arxiv_id(paper):
    """Only explicit arXiv identifiers/URLs; a DOI digit fragment is not an ID."""
    explicit=str(paper.get('arxiv_id') or '').strip()
    match=re.fullmatch(r'(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?',explicit,re.I)
    if not match:
        value=' '.join(str(paper.get(k) or '') for k in ('id','url','open_access_pdf_url'))
        match=re.search(r'(?:arxiv:|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?(?=$|[\s/?#])',value,re.I)
    if not match:return None
    value=match.group(1)
    year,month=2000+int(value[:2]),int(value[2:4])
    return value if 2007<=year<=research_today().year and 1<=month<=12 else None


def normalize_publication_metadata(paper):
    """Use first public release when known, preserving day/month/year precision."""
    item=dict(paper)
    arxiv_id=paper_arxiv_id(item)
    if not arxiv_id:return item
    month=f'20{arxiv_id[:2]}-{arxiv_id[2:4]}'
    raw=str(item.get('publication_date') or '')
    source=str(item.get('date_source') or '')
    # The identifier certifies a month, never the first day of that month.
    if not source.startswith('arxiv') and (not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?',raw) or raw[:7]>=month):
        item.update(publication_date=month,date_source='arxiv_identifier_month',year=int(month[:4]))
    elif not raw:
        item.update(publication_date=month,date_source='arxiv_identifier_month',year=int(month[:4]))
    return item


def publication_scope(paper, scope):
    if not scope:
        return 'unrestricted', ''
    paper=normalize_publication_metadata(paper)
    start, end = date.fromisoformat(scope['start']), date.fromisoformat(scope['end'])
    raw = str(paper.get('publication_date') or '')
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
            published = date.fromisoformat(raw)
            return ('in_scope','已核实日期') if start<=published<=end else ('outside_scope','发布日期在时间窗外')
        if re.fullmatch(r'\d{4}-\d{2}',raw):
            year,month=map(int,raw.split('-'))
            low,high=date(year,month,1),date(year,month,calendar.monthrange(year,month)[1])
            if high<start or low>end:return 'outside_scope','发表月份在时间窗外'
            if start<=low and high<=end:return 'in_scope','整月在时间窗内，仅有月份'
            return 'date_unconfirmed','发表月份跨越时间边界，缺少具体日期'
        year = int(paper.get('year') or raw[:4])
        low, high = date(year,1,1), date(year,12,31)
        if high < start or low > end:
            return 'outside_scope', '年份在时间窗外'
        if start<=low and high<=end:
            return 'in_scope', '整年在时间窗内，仅有年份'
    except (TypeError, ValueError):
        pass
    return 'date_unconfirmed', '缺少精确发布日期，不能确认为时间窗内结果'


def scoped_papers(papers, direction):
    plan=plan_for(direction)
    scope = plan['time_scope']
    included, excluded = [], []
    for paper in papers:
        paper=normalize_publication_metadata(paper)
        status, reason = publication_scope(paper,scope)
        item = dict(paper, temporal_status=status, temporal_reason=reason)
        if status=='outside_scope' and plan.get('source_scope')!='provided' and not comparison_all_time(plan):
            excluded.append({'id':paper['id'],'title':paper.get('title'), 'year':paper.get('year'),
                             'publication_date':paper.get('publication_date'), 'reason':reason})
        else:
            if status=='outside_scope':item['comparison_context_only']=True
            included.append(item)
    included.sort(key=lambda p:p['temporal_status']=='date_unconfirmed')
    return included, excluded


def comparison_all_time(plan, group=None):
    groups=(plan.get('comparison') or {}).get('all_time_groups') or []
    return plan.get('task_type')=='benchmark_comparison' and bool(groups) and (
        group is None or '*' in groups or group in groups)


def comparison_row_scope(profile, plan, group):
    status,reason=publication_scope(profile,plan.get('time_scope') or {})
    if comparison_all_time(plan,group):
        published=str(normalize_publication_metadata(profile).get('publication_date') or profile.get('year') or '')
        end=(plan.get('time_scope') or {}).get('end') or research_today().isoformat()
        if re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?',published) and published>end[:len(published)]:
            return 'outside_scope','发布日期晚于本轮研究日期'
        return 'unrestricted','用户要求此分组比较全部模型，不限较早发表年份'
    return status,reason
