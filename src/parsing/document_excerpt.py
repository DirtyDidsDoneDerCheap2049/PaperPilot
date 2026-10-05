"""Bounded verbatim excerpts selected across a document, not just its prefix."""
import math
import re


def document_excerpt(text,query,max_chars):
    if len(text)<=max_chars:return text
    if max_chars<400:
        note='\n[原文摘录已截断，不能据此判断全文没有相关信息]'
        return text[:max(0,max_chars-len(note))]+note[:max_chars]
    terms=list(dict.fromkeys(re.findall(r'[a-z][a-z0-9_-]{2,}|\d+\.\d+|[\u4e00-\u9fff]{2,6}',query.casefold())))[:80]
    chunks=[(start,min(len(text),start+1600)) for start in range(0,len(text),1600)]
    tokens=[text[a:b].casefold() for a,b in chunks]
    weights={term:math.log(1+len(chunks)/(1+sum(term in chunk for chunk in tokens))) for term in terms}
    ranked=sorted(range(len(chunks)),key=lambda i:sum(weights[t] for t in terms if t in tokens[i]),reverse=True)
    spans=[(0,min(700,max_chars//5)),(len(text)-min(500,max_chars//5),len(text))]
    used=sum(b-a for a,b in spans)+250
    for i in ranked:
        a,b=chunks[i]
        if any(a<end and b>start for start,end in spans):continue
        score=sum(weights[t] for t in terms if t in tokens[i])
        if not score:continue
        if used+(b-a)+60>max_chars:continue
        spans.append((a,b));used+=b-a+60
    parts=[f'[原文字符 {a+1}–{b}]\n{text[a:b]}' for a,b in sorted(spans)]
    return '[这是跨全文选取的有限摘录；省略部分可能含相关结果，不能据此断言原文没有。]\n'+'\n\n[中间省略]\n\n'.join(parts)
