"""Model-assisted library organization without deleting papers or their evidence.

Identity is decided locally; the model only supplies categories, tags and short
reading summaries. Original IDs remain valid for historical citations.
"""
import hashlib
import json
import re
import unicodedata
from pathlib import Path

BATCH_SIZE = 12
MAX_PAPERS = 1500
LIBRARY_SESSION = '__paper_library__'
UNKNOWN = '待分类'
POLICY_ROOT = Path(__file__).parent / 'policies' / 'library-organizer'
IDENTITY_FIELDS = ('id', 'title', 'authors_json', 'year', 'doi', 'arxiv_id',
                   'semantic_scholar_id', 'url', 'abstract', 'retrieval_status',
                   'fulltext_path', 'parsed_markdown_path', 'parsed_json_path')


def decode(value, default):
    try:
        return json.loads(value) if isinstance(value, str) else value if value is not None else default
    except (ValueError, TypeError):
        return default


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def load_policy(workspace=None):
    """Missing packaged instructions are an error, never a silent weaker fallback."""
    files = ('SKILL.md', 'references/taxonomy.md', 'references/classification.md', 'references/local-facets.json')
    try:
        parts = [POLICY_ROOT.joinpath(name).read_text(encoding='utf-8') for name in files]
    except OSError as exc:
        raise ValueError('论文整理规则文件缺失，请更新或重新安装完整软件包') from exc
    if any(not part.strip() for part in parts):
        raise ValueError('论文整理规则文件为空，本次没有调用模型')
    categories=None
    if workspace:
        path=Path(workspace)/'notes/library-taxonomy.json'
        if path.exists():
            try: categories=valid_taxonomy(json.loads(path.read_text(encoding='utf-8')))
            except (OSError,ValueError,TypeError) as exc:
                raise ValueError('固定分类目录无法读取，请检查 notes/library-taxonomy.json；没有调用模型') from exc
    digest='\n'.join(parts)+('\n'+encode(categories) if categories else '')
    return {'version': '2', 'sha256':hashlib.sha256(digest.encode()).hexdigest(),
            'system':parts[0], 'taxonomy':parts[1], 'classification':parts[2], 'categories':categories}


def snapshot(db):
    rows = db.fetchall("SELECT * FROM papers WHERE id NOT LIKE 'prior:%' AND COALESCE(retrieval_status,'') != 'off_topic' ORDER BY id")
    profiles = {r['paper_id']: r for r in db.fetchall('SELECT * FROM innovation_profiles')}
    return rows, profiles


def fingerprint(rows, profiles, workspace=None):
    values = [{**{k: r.get(k) for k in IDENTITY_FIELDS},
               'profile': (profiles.get(r['id']) or {}).get('profile_json')} for r in rows]
    if workspace is not None:
        files = []
        root = Path(workspace).resolve()
        for row in rows:
            for field in ('fulltext_path','parsed_markdown_path'):
                path = row.get(field)
                signature = None
                if path:
                    try:
                        file = (root / path).resolve()
                        file.relative_to(root)
                        stat = file.stat()
                        signature = (stat.st_size,stat.st_mtime_ns)
                    except (OSError,ValueError):
                        pass
                files.append((row['id'],field,signature))
        values.append({'source_files':files})
    return hashlib.sha256(encode(values).encode()).hexdigest()


def normalized(text):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', str(text or '')).casefold()).replace('_', '')


def identifiers(row):
    doi = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', str(row.get('doi') or '').strip(), flags=re.I).lower().rstrip(' .')
    arxiv = str(row.get('arxiv_id') or '')
    if not arxiv:
        match = re.search(r'arxiv\.org/(?:abs|pdf)/([^?#]+)', str(row.get('url') or ''), re.I)
        arxiv = match[1] if match else ''
    arxiv = re.sub(r'^(?:arxiv:|https?://arxiv\.org/(?:abs|pdf)/)', '', arxiv, flags=re.I)
    arxiv = re.sub(r'(?:\.pdf)?$', '', arxiv, flags=re.I)
    arxiv = re.sub(r'v\d+$', '', arxiv, flags=re.I).strip().lower()
    if not re.fullmatch(r'10\.\d+/.+',doi):
        doi = ''
    if not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})',arxiv):
        arxiv = ''
    semantic = str(row.get('semantic_scholar_id') or '').strip().lower()
    if semantic in {'unknown','none','null','n/a','-'}:
        semantic = ''
    return {'doi': doi, 'arxiv': arxiv, 'semantic': semantic}


def compatible(group, row):
    incoming = identifiers(row)
    return all(not incoming[k] or not identifiers(other)[k] or incoming[k] == identifiers(other)[k]
               for other in group for k in incoming)


def author_names(row):
    authors = decode(row.get('authors_json'), [])
    if not isinstance(authors, list):
        return set()
    return {normalized(a.get('name') if isinstance(a, dict) else a) for a in authors} - {''}


def local_pdf_hash(workspace, row, cache):
    if not row.get('fulltext_path'):
        return ''
    try:
        path = (Path(workspace) / row['fulltext_path']).resolve()
        path.relative_to(Path(workspace).resolve())
        if path.suffix.lower() != '.pdf' or not path.is_file():
            return ''
        if path not in cache:
            with path.open('rb') as stream:
                cache[path] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return cache[path]
    except (OSError, ValueError):
        return ''


def duplicate_groups(rows, profiles, workspace):
    """Union only exact identities, checking the whole component for conflicts."""
    groups = {r['id']: [r] for r in rows}
    owner = {r['id']: r['id'] for r in rows}
    keys, hashes, title_groups = {}, {}, {}
    for row in rows:
        rid = row['id']
        ids = identifiers(row)
        candidates = [(k, v) for k, v in ids.items() if v]
        pdf = local_pdf_hash(workspace, row, hashes)
        if pdf:
            candidates.append(('pdf', pdf))
        title = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC',str(row.get('title') or '')).casefold()).strip().rstrip('.')
        if len(title) >= 16:
            # Exact title alone is insufficient. Same year + a shared author is required.
            for old in title_groups.get(title, []):
                if row.get('year') and row.get('year') == old.get('year') and author_names(row) and author_names(row) == author_names(old):
                    candidates.append(('title_author', old['id']))
                    keys.setdefault(('title_author', old['id']), []).append(old['id'])
            title_groups.setdefault(title, []).append(row)
        for key in candidates:
            for previous in keys.get(key, []):
                left, right = owner[rid], owner[previous]
                if left == right:
                    continue
                if all(compatible(groups[left], other) for other in groups[right]):
                    members = groups.pop(right)
                    groups[left].extend(members)
                    for member in members:
                        owner[member['id']] = left
            keys.setdefault(key, []).append(rid)
    result = []
    for members in groups.values():
        def quality(row):
            return (bool(row.get('parsed_markdown_path')), bool(profiles.get(row['id'])),
                    bool(row.get('fulltext_path')), len(row.get('abstract') or ''), row['id'])
        members.sort(key=quality, reverse=True)
        result.append(members)
    result.sort(key=lambda g: g[0]['id'])
    # Exact titles with insufficient/conflicting identifiers remain separate.
    suspicious = []
    for members in title_groups.values():
        roots = sorted({owner[r['id']] for r in members})
        if len(roots) > 1:
            suspicious.append({'title': members[0]['title'], 'paper_ids': [r['id'] for r in members]})
    return result, suspicious


def organization_rows(db):
    return {r['paper_id']: r for r in db.fetchall('SELECT * FROM paper_organization ORDER BY paper_id')}


def canonical_id(db, paper_id):
    row = db.fetchone('SELECT canonical_id FROM paper_organization WHERE paper_id=?', (paper_id,))
    return row['canonical_id'] if row else paper_id


def organization_view(row):
    from src.knowledge.library_facets import paper_facets
    facets,source=paper_facets(row)
    return {'facets':facets,'facet_source':source,'category': row.get('category') or ('先验笔记' if str(row.get('id','')).startswith('prior:') else UNKNOWN), 'tags': decode(row.get('tags_json'), []),
            'reading_summary': row.get('summary') or '', 'canonical_id': row.get('canonical_id') or row['id']}


def descriptor(group, profiles, workspace):
    row = group[0]
    abstracts = [r.get('abstract') for r in group if r.get('abstract')]
    profile = decode((profiles.get(row['id']) or {}).get('profile_json'), {})
    keys = ('research_task', 'problem', 'method_category', 'method_subcategory', 'key_techniques', 'innovation_detail')
    excerpt = {k: profile[k] for k in keys if isinstance(profile, dict) and profile.get(k)}
    parsed = ''
    if row.get('parsed_markdown_path'):
        try:
            path = (Path(workspace) / row['parsed_markdown_path']).resolve()
            path.relative_to(Path(workspace).resolve())
            if path.suffix.lower() in {'.md','.txt'}:
                with path.open(encoding='utf-8') as stream:
                    parsed = stream.read(2400)
        except (OSError, ValueError):
            pass
    return {'id': row['id'], 'title': str(row.get('title') or '')[:400],
            'abstract': str(max(abstracts, key=len) if abstracts else '')[:1800],
            'analysis_excerpt': encode(excerpt)[:2000], 'parsed_excerpt':parsed}


def valid_taxonomy(value):
    categories = value.get('categories') if isinstance(value, dict) else None
    if not isinstance(categories, list) or not 1 <= len(categories) <= 16:
        raise ValueError('模型分类目录不完整，请重试整理')
    if any(not isinstance(c, str) or not c.strip() or len(c.strip()) > 36 for c in categories):
        raise ValueError('模型分类名称不合法，请重试整理')
    return list(dict.fromkeys([c.strip() for c in categories] + [UNKNOWN]))


def valid_assignments(value, expected, categories):
    items = value.get('papers') if isinstance(value, dict) else None
    if not isinstance(items, list) or len(items) != len(expected):
        raise ValueError('模型遗漏了论文，本次整理未应用')
    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'),str) or item['id'] not in expected or item['id'] in result:
            raise ValueError('模型返回了重复或未知论文 ID，本次整理未应用')
        category, tags, summary = item.get('category'), item.get('tags'), item.get('summary')
        if category not in categories or not isinstance(tags, list) or len(tags) > 5:
            raise ValueError('模型分类或标签不合法，本次整理未应用')
        if any(not isinstance(t, str) or not t.strip() or len(t) > 36 for t in tags):
            raise ValueError('模型标签不合法，本次整理未应用')
        if not isinstance(summary, str) or len(summary) > 240:
            raise ValueError('模型阅读摘要过长或缺失，本次整理未应用')
        from src.knowledge.library_facets import validate_facets
        facets=validate_facets(item.get('facets',{}))
        result[item['id']] = dict(category=category, tags=list(dict.fromkeys(t.strip() for t in tags)), summary=summary.strip(),facets=facets)
    return result


def incremental_preview(db, rows, profiles, workspace, policy=None, groups=None):
    """Reuse classifications only with matching rules and recorded input identity."""
    policy=policy or load_policy(workspace)
    groups=groups if groups is not None else duplicate_groups(rows,profiles,workspace)[0]
    signatures={g[0]['id']:fingerprint(g,profiles,workspace) for g in groups}
    latest=db.fetchone("SELECT * FROM library_organization_runs WHERE state='applied' ORDER BY applied_at DESC,rowid DESC LIMIT 1")
    base=decode((latest or {}).get('plan_json'),{})
    categories=base.get('categories')
    current=organization_rows(db)
    assignments={}
    if base.get('policy_sha256')==policy['sha256'] and isinstance(categories,list) and categories:
        # Old releases stored one full-library fingerprint. Exact equality safely establishes
        # per-paper baselines without sending the just-organized library to the model again.
        legacy_unchanged=bool(latest and latest['input_hash']==fingerprint(rows,profiles,workspace))
        for group in groups:
            pid=group[0]['id']
            if not legacy_unchanged and base.get('source_hashes',{}).get(pid)!=signatures[pid]:continue
            expected=base.get('entries',{})
            if any(member['id'] not in expected or any(current.get(member['id'],{}).get(k)!=v for k,v in expected[member['id']].items()) for member in group):continue
            item=current.get(pid,{})
            candidate={'id':pid,'category':item.get('category'),'tags':decode(item.get('tags_json'),[]),'summary':item.get('summary'),'facets':decode(item.get('facets_json'),{})}
            try:assignments.update(valid_assignments({'papers':[candidate]},{pid},categories))
            except ValueError:continue
    else:categories=policy.get('categories')
    pending=len(groups)-len(assignments)
    return {'categories':categories,'assignments':assignments,'source_hashes':signatures,
            'reused_papers':len(assignments),'pending_papers':pending,
            'estimated_calls':(0 if categories else 1)+(pending+BATCH_SIZE-1)//BATCH_SIZE}


async def organize_library(db, llm, workspace, run_id, progress):
    policy = load_policy(workspace)
    rows, profiles = snapshot(db)
    if not rows:
        raise ValueError('论文库中没有可整理的论文')
    if len(rows) > MAX_PAPERS:
        raise ValueError(f'单轮整理最多 {MAX_PAPERS} 条记录，请先缩小论文库；没有静默截断')
    digest = fingerprint(rows, profiles, workspace)
    before = organization_rows(db)
    previous = db.fetchone('SELECT * FROM library_organization_runs WHERE id=?', (run_id,))
    if previous and previous['state'] == 'applied':
        return decode(previous['plan_json'], {})['result']
    if previous and previous['state'] == 'undone':
        raise ValueError('这次整理已经撤销，请新建整理任务')
    old_plan = decode((previous or {}).get('plan_json'), {})
    reusable = previous and previous['input_hash'] == digest and decode(previous['before_json'], {}) == before and old_plan.get('policy_sha256') == policy['sha256']
    groups, suspicious = duplicate_groups(rows, profiles, workspace)
    preview=incremental_preview(db,rows,profiles,workspace,policy,groups)
    plan = decode(previous['plan_json'], {}) if reusable else {
        'categories':preview['categories'],'assignments':preview['assignments'],
        'reused_papers':preview['reused_papers']}
    plan['source_hashes']=preview['source_hashes']
    plan.update(policy_version=policy['version'], policy_sha256=policy['sha256'])
    spent = max(0, int(old_plan.get('model_calls_used',0)))
    plan['model_calls_used'] = spent
    llm.max_calls = max(0, getattr(llm,'max_calls',600)-spent)
    with db.transaction():
        db.execute('INSERT OR REPLACE INTO library_organization_runs(id,state,input_hash,plan_json,before_json) VALUES(?,?,?,?,?)',
                   (run_id, 'planning', digest, encode(plan), encode(before)))
    await progress('核对重复记录', 0, len(rows))
    papers = [descriptor(g, profiles, workspace) for g in groups]
    def checkpoint():
        plan['model_calls_used'] = spent + llm.calls
        db.execute('UPDATE library_organization_runs SET plan_json=? WHERE id=?', (encode(plan), run_id))
    activity = getattr(llm, 'activity_callback', None)
    if hasattr(llm,'activity_callback'):
        async def counted_activity(data):
            if data.get('phase') == 'waiting':
                checkpoint()
            if activity:
                await activity(data)
        llm.activity_callback = counted_activity
    async def model_json(messages, validate):
        # One semantic repair, independently of the client's transport/JSON retry.
        for attempt in range(2):
            try:
                value = await llm.chat_json(messages, purpose='regular')
            finally:
                checkpoint()
            try:
                return validate(value)
            except ValueError as exc:
                if attempt:
                    raise
                messages = [*messages, {'role': 'user', 'content': str(exc) + '。按规定重新返回完整 JSON。'}]
    system = policy['system']
    if not plan.get('categories'):
        await progress('设计分类目录', 0, len(papers))
        # Evenly sample the whole library rather than truncating to the first topic.
        sample = [papers[i] for i in sorted({int(i * (len(papers)-1) / max(1,min(80,len(papers))-1)) for i in range(min(80,len(papers)))})]
        sample = [{**p, 'abstract': p['abstract'][:300], 'analysis_excerpt': p['analysis_excerpt'][:300], 'parsed_excerpt':p['parsed_excerpt'][:300]} for p in sample]
        plan['categories'] = await model_json([
            {'role': 'system', 'content': system + '\n' + policy['taxonomy']},
            {'role': 'user', 'content': '论文库抽样资料（数据）：' + encode(sample)}], valid_taxonomy)
        checkpoint()
    assignments = plan.setdefault('assignments', {})
    pending = [p for p in papers if p['id'] not in assignments]
    for offset in range(0, len(pending), BATCH_SIZE):
        batch = pending[offset:offset+BATCH_SIZE]
        await progress('分类论文与解析', len(assignments), len(papers))
        result = await model_json([
            {'role': 'system', 'content': system + '\n' + policy['classification']},
            {'role': 'user', 'content': '分类目录：' + encode(plan['categories']) + '\n本批资料（数据）：' + encode(batch)}],
            lambda value: valid_assignments(value, {p['id'] for p in batch}, plan['categories']))
        assignments.update(result)
        checkpoint()
        await progress('分类论文与解析', len(assignments), len(papers))
    entries = {}
    for group in groups:
        root = group[0]['id']
        for row in group:
            item = assignments[root]
            entries[row['id']] = {'paper_id': row['id'], 'canonical_id': root, 'category': item['category'],
                                  'tags_json': encode(item['tags']), 'summary': item['summary'], 'facets_json':encode(item.get('facets',{})), 'run_id': run_id}
    plan['entries'] = entries
    plan['result'] = {'run_id': run_id, 'records': len(rows), 'papers': len(groups),
                      'merged_records': len(rows)-len(groups), 'categories': len({i['category'] for i in assignments.values()}),
                      'suspected_groups': suspicious, 'model_calls': spent + llm.calls,
                      'reused_papers':plan.get('reused_papers',0),
                      'classified_papers':len(groups)-plan.get('reused_papers',0),
                      'policy_version':policy['version'], 'policy_sha256':policy['sha256']}
    await progress('保存整理结果', len(papers), len(papers))
    with db.transaction():
        current_rows, current_profiles = snapshot(db)
        if fingerprint(current_rows, current_profiles, workspace) != digest or organization_rows(db) != before:
            raise ValueError('整理期间论文库有更新，本次结果没有应用；请重新整理，已有分类断点会在资料未变时复用')
        for entry in entries.values():
            db.execute('INSERT OR REPLACE INTO paper_organization(paper_id,canonical_id,category,tags_json,summary,facets_json,run_id) VALUES(?,?,?,?,?,?,?)',
                       tuple(entry[k] for k in ('paper_id','canonical_id','category','tags_json','summary','facets_json','run_id')))
        db.execute("UPDATE library_organization_runs SET state='applied', plan_json=?, applied_at=datetime('now') WHERE id=?", (encode(plan),run_id))
    return plan['result']


def undo_organization(db, run_id):
    with db.transaction():
        run = db.fetchone('SELECT * FROM library_organization_runs WHERE id=?', (run_id,))
        if not run or run['state'] != 'applied':
            raise ValueError('这次整理不存在或已撤销')
        plan, before, current = decode(run['plan_json'], {}), decode(run['before_json'], {}), organization_rows(db)
        entries = plan.get('entries', {})
        for pid, expected in entries.items():
            actual = current.get(pid, {})
            if any(actual.get(k) != v for k,v in expected.items()):
                raise ValueError('已有更新的整理结果，请先撤销最新一次整理')
        for pid in entries:
            db.execute('DELETE FROM paper_organization WHERE paper_id=?', (pid,))
            if pid in before:
                old = before[pid]
                db.execute('INSERT INTO paper_organization(paper_id,canonical_id,category,tags_json,summary,facets_json,run_id,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                           tuple(old.get(k) for k in ('paper_id','canonical_id','category','tags_json','summary','facets_json','run_id','updated_at')))
        db.execute("UPDATE library_organization_runs SET state='undone',undone_at=datetime('now') WHERE id=?", (run_id,))
    return {'status': 'undone', 'run_id': run_id}
