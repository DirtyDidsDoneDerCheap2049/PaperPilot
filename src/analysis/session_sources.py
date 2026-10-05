"""Recover same-session paper references independently of dialogue truncation."""
import json


def _object(value):
    try:value=json.loads(value) if isinstance(value,str) else value
    except (TypeError,ValueError):return {}
    return value if isinstance(value,dict) else {}


def attachment_paper_ids(db,session_id,before_message_id='',limit=100):
    if not db or not session_id:return []
    anchor=db.fetchone('SELECT rowid AS seq FROM messages WHERE session_id=? AND id=?',
                       (session_id,before_message_id)) if before_message_id else None
    condition=' AND rowid<?' if anchor else ''
    base=(session_id,anchor['seq']) if anchor else (session_id,)
    ids=[];seen=set();offset=0
    # Metadata can outlive the bounded model dialogue. Do not read text bodies,
    # snapshot paper catalogs or references from another session.
    while len(ids)<limit:
        rows=db.fetchall('SELECT metadata_json FROM messages WHERE session_id=?'+condition+
                         ' AND metadata_json IS NOT NULL ORDER BY rowid DESC LIMIT ? OFFSET ?',base+(128,offset))
        for row in rows:
            selected=_object(row.get('metadata_json')).get('selected_paper_ids')
            if not isinstance(selected,list):continue
            for pid in selected:
                if isinstance(pid,str) and pid and pid not in seen:
                    ids.append(pid);seen.add(pid)
        if len(rows)<128:break
        offset+=128
    return ids[:limit]


def research_paper_ids(db,session_id,before_message_id='',limit=100):
    if not db or not session_id:return []
    anchor=db.fetchone('SELECT created_at FROM messages WHERE session_id=? AND id=?',
                       (session_id,before_message_id)) if before_message_id else None
    condition=' AND created_at<=?' if anchor else ''
    args=(session_id,anchor['created_at']) if anchor else (session_id,)
    rows=db.fetchall("SELECT search_log_json FROM gap_analyses WHERE session_id=? AND id NOT LIKE 'checkpoint_%'"+
                     condition+' ORDER BY created_at DESC,rowid DESC LIMIT 8',args)
    for row in rows:
        log=_object(row.get('search_log_json'))
        explicit=log.get('selected_paper_ids') or []
        papers=log.get('papers') or []
        ids=[pid for pid in explicit if isinstance(pid,str) and pid] if isinstance(explicit,list) else []
        if isinstance(papers,list):ids.extend(p['id'] for p in papers if isinstance(p,dict) and isinstance(p.get('id'),str) and p['id'])
        if ids:return list(dict.fromkeys(ids))[:limit]
    return []
