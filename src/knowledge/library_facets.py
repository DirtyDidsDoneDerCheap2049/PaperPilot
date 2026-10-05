"""Independent browsing dimensions. Absence of a cue is never negative evidence."""
import json
import re
from functools import lru_cache
from pathlib import Path

FACETS = {
    'temporal': {'label':'输入时序', 'values':['单帧','多帧 / 时序']},
    'prior': {'label':'先验来源', 'values':['单目深度先验','基础模型先验','几何先验','外部传感器先验']},
    'training': {'label':'训练方式', 'values':['有监督','自监督','无监督','半监督']},
    'mechanism': {'label':'方法机制', 'values':['知识蒸馏','先验融合','跨任务迁移']},
}
UNSPECIFIED = '未标明'


@lru_cache(maxsize=1)
def local_rules():
    path=Path(__file__).parent/'policies/library-organizer/references/local-facets.json'
    return json.loads(path.read_text(encoding='utf-8'))


def validate_facets(value):
    if not isinstance(value,dict) or set(value)-set(FACETS):
        raise ValueError('筛选维度不合法')
    result = {}
    for key,spec in FACETS.items():
        items = value.get(key,[])
        if not isinstance(items,list) or any(v not in spec['values'] for v in items):
            raise ValueError('筛选标签不在规定目录中')
        result[key] = list(dict.fromkeys(items))
    if len(result['temporal'])>1:
        raise ValueError('单帧和多帧不能同时标注；训练视频不等于推理多帧')
    return result


def local_facets(row):
    """Positive title/tag cues for legacy rows; never infer single-frame by omission.

    Deliberately exclude generated summaries and unstructured profile conclusions.
    A temporal dataset or event stream is not evidence of multiframe inference.
    """
    try: tags=json.loads(row.get('tags_json') or '[]')
    except (ValueError,TypeError): tags=[]
    if not isinstance(tags,list): tags=[]
    title=str(row.get('title') or '').lower()
    text=' '.join([title,*[str(t).lower() for t in tags]])
    result={k:[] for k in FACETS}
    for rule in local_rules():
        key,value=rule['key'],rule['value']
        if rule.get('only_if_empty') and result[key]:continue
        if rule.get('require_title') and not re.search(rule['require_title'],title):continue
        if rule.get('exclude_title') and re.search(rule['exclude_title'],title):continue
        if rule.get('exclude') and re.search(rule['exclude'],text):continue
        if re.search(rule['pattern'],text) and value not in result[key]:result[key].append(value)
    return result


def paper_facets(row):
    try:
        stored=json.loads(row.get('facets_json') or '{}')
        if stored: return validate_facets(stored), '模型或人工整理'
    except (ValueError,TypeError): pass
    return local_facets(row), '标题与已有标签'


def matches_facets(row,selected):
    values,_=paper_facets(row)
    return all((not values.get(k) if v==UNSPECIFIED else v in values.get(k,[])) for k,v in selected.items())


def facet_counts(rows):
    result=[]
    for key,spec in FACETS.items():
        counts={v:0 for v in [*spec['values'],UNSPECIFIED]}
        for row in rows:
            values,_=paper_facets(row)
            for value in values[key] or [UNSPECIFIED]:counts[value]+=1
        result.append({'key':key,'label':spec['label'],'values':[{'name':v,'count':n} for v,n in counts.items()]})
    return result
