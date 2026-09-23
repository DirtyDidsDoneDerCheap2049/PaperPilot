"""Bind quotes to source text; a match is not semantic entailment."""
import copy
import hashlib
import re

def bind_profile(profile,text,source):
    result=copy.deepcopy(profile)
    digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
    chars=[]; offsets=[]
    for index,char in enumerate(text):
        if not char.isspace():
            chars.append(char); offsets.append(index)
    normalized=''.join(chars)
    result['document_sha256']=digest
    result['source_document']=source
    result['evidence_verified']=True
    evidence=[]
    for original in result.get('evidence',[]):
        item=dict(original) if isinstance(original,dict) else {'quote':str(original)}
        quote=str(item.get('quote') or '')
        key=re.sub(r'\s+','',quote)
        start=normalized.find(key) if len(key)>=12 else -1
        item['source_verified']=start>=0
        item.pop('source_span',None)
        if start>=0:
            begin,end=offsets[start],offsets[start+len(key)-1]+1
            item['source_span']={'document':source,'sha256':digest,'start':begin,'end':end,'text':text[begin:end]}
        evidence.append(item)
    result['evidence']=evidence
    return result
