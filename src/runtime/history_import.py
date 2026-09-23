"""Import a closed legacy SQLite workspace into a new, isolated local workspace.

The source is never opened for writing. No credentials or old task queue are
activated. An existing destination is never merged or overwritten.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import uuid
from contextlib import closing

import yaml


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relocate(value, source, destination):
    if isinstance(value, dict):
        return {k: relocate(v, source, destination) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate(v, source, destination) for v in value]
    if isinstance(value, str):
        normalized = value.replace('\\', '/')
        prefix = source.as_posix()
        if normalized.casefold() == prefix.casefold():
            return destination.as_posix()
        if normalized.casefold().startswith(prefix.casefold() + '/'):
            return destination.as_posix() + normalized[len(prefix):]
    return value


def import_history(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('历史源与目标必须互相独立')
    marker = destination / '.history-import.json'
    if destination.exists():
        if marker.is_file() and json.loads(marker.read_text(encoding='utf-8')).get('source') == str(source):
            if not (destination/'db/ai_reader.db').is_file():
                raise ValueError('导入副本缺少数据库，请从备份恢复，不能自动覆盖')
            return json.loads(marker.read_text(encoding='utf-8'))
        raise ValueError('目标已存在；为保护已有记录，不自动合并或覆盖')
    if (source/'.database.json').exists():
        raise ValueError('此入口只导入旧 SQLite 工作区，MySQL 工作区请使用专用备份迁移')
    source_db = source/'db/ai_reader.db'
    if not source_db.is_file() or not (source/'config.yaml').is_file():
        raise ValueError('请选择包含 config.yaml 与 db/ai_reader.db 的历史工作区')
    # The legacy app has no common process lock. Refuse live WAL snapshots,
    # and detect changes during the copy rather than pretending file/DB atomicity.
    if any((source/'db'/name).exists() for name in ('ai_reader.db-wal','ai_reader.db-journal')):
        raise ValueError('请先关闭使用历史工作区的程序，再执行导入')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Inherit workspace ACLs on Windows. tempfile.mkdtemp(mode=0700) can
    # restrict access to a sandbox identity and lock out the desktop user.
    staging = destination.parent / ('.history-import-' + uuid.uuid4().hex)
    staging.mkdir()
    original_hash = digest(source_db)
    manifest = {}
    try:
        for folder in ('papers','reports','exports','notes'):
            base = source/folder
            if not base.exists():
                continue
            for item in base.rglob('*'):
                if item.is_symlink() or not item.resolve().is_relative_to(source):
                    raise ValueError('源工作区含外部链接，请先整理后导入')
                if not item.is_file():
                    continue
                relative = item.relative_to(source)
                target = staging/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                before = digest(item)
                shutil.copy2(item, target)
                if digest(target) != before or digest(item) != before:
                    raise RuntimeError('复制期间源资料发生变化，请关闭旧程序后重试')
                manifest[relative.as_posix()] = before
        config = yaml.safe_load((source/'config.yaml').read_text(encoding='utf-8')) or {}
        def without_secrets(value):
            if isinstance(value, dict):
                return {k:without_secrets(v) for k,v in value.items()
                        if not any(word in str(k).lower() for word in ('api_key','token','password','secret'))}
            if isinstance(value,list):return [without_secrets(v) for v in value]
            return value
        config = without_secrets(config)
        config['embedding'] = {'provider':'local'}
        config.setdefault('mineru',{})['enabled'] = False
        (staging/'config.yaml').write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False),encoding='utf-8')
        (staging/'db').mkdir(exist_ok=True)
        with closing(sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True)) as original:
            with closing(sqlite3.connect(staging/'db/ai_reader.db')) as copied:
                original.backup(copied)
                if copied.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('历史数据库完整性校验失败')
                tables=[r[0] for r in copied.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                counts={}
                for table in tables:
                    quoted='"'+table.replace('"','""')+'"'
                    counts[table]=original.execute(f'SELECT count(*) FROM {quoted}').fetchone()[0]
                    assert copied.execute(f'SELECT count(*) FROM {quoted}').fetchone()[0] == counts[table]
                    columns=[r[1] for r in copied.execute(f'PRAGMA table_info({quoted})')]
                    for column in columns:
                        if not ('path' in column or column.endswith('_json')):
                            continue
                        cq='"'+column.replace('"','""')+'"'
                        for rowid,value in copied.execute(f'SELECT rowid,{cq} FROM {quoted}').fetchall():
                            if not isinstance(value,str):continue
                            try:
                                decoded=json.loads(value) if column.endswith('_json') else value
                            except ValueError:continue
                            adjusted=relocate(decoded,source,destination)
                            if adjusted != decoded:
                                copied.execute(f'UPDATE {quoted} SET {cq}=? WHERE rowid=?',
                                    (json.dumps(adjusted,ensure_ascii=False) if column.endswith('_json') else adjusted,rowid))
                if 'sessions' in tables:
                    copied.execute("UPDATE sessions SET workspace_id=?,status='idle'",(str(uuid.uuid5(uuid.NAMESPACE_DNS,str(destination))),))
                if 'agent_runs' in tables:
                    copied.execute("UPDATE agent_runs SET status='interrupted' WHERE status='running'")
                copied.commit()
        from src.knowledge.vector_store import LocalSearchStore, VectorDoc
        index=LocalSearchStore(staging/'db/text_index.db')
        with closing(sqlite3.connect(staging/'db/ai_reader.db')) as copied:
            copied.row_factory=sqlite3.Row
            if 'papers' in tables:
                docs=[VectorDoc(id=r['id'],text=f"{r['title'] or ''}\n{r['abstract'] or ''}",metadata={'has_fulltext':str(bool(r['parsed_markdown_path']))}) for r in copied.execute('SELECT * FROM papers')]
                index.add_documents('papers',docs)
                index.add_documents('notes',docs)
            if 'innovation_profiles' in tables:
                index.add_documents('innovation_profiles',[VectorDoc(id=r['paper_id'],text=r['profile_json'],metadata={'paper_id':r['paper_id']}) for r in copied.execute('SELECT * FROM innovation_profiles')])
            if 'evidence_chunks' in tables:
                index.add_documents('evidence_chunks',[VectorDoc(id=r['id'],text=r['content'],metadata={'paper_id':r['paper_id'],'source_type':r['source_type'] or ''}) for r in copied.execute('SELECT * FROM evidence_chunks')])
        index.close()
        if digest(source_db) != original_hash:
            raise RuntimeError('导入期间历史数据库发生变化，请关闭旧程序后重试')
        report={'version':1,'source':str(source),'destination':str(destination),
                'source_database_sha256':original_hash,'rows':counts,'files':len(manifest),
                'file_hashes':manifest,'credentials_copied':False,
                'limitations':['旧任务队列与向量缓存不导入','报告正文不改写，正文内旧绝对路径可能仍指向源目录','模型 API 需要在新工作区重新配置']}
        (staging/'.history-import.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        # rename is non-overwriting on Windows; staging and destination share a volume.
        if destination.exists():raise ValueError('目标在导入期间被创建，停止以免覆盖')
        staging.rename(destination)
        return report
    finally:
        if staging.exists():shutil.rmtree(staging)
