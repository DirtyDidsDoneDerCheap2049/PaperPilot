"""Capture current UI with isolated, explicitly fictional documentation fixtures.

Requires the development Python environment, the built native task executable,
Node.js, Playwright and installed Chrome. No model calls or private workspace
imports are made. The browser shares the desktop's real FastAPI/frontend.
"""
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / 'work/readme-capture'
WORKSPACE = WORK / 'workspace'
SESSION = 'screenshot:research'
MESSAGE = 'screenshot:question'
REPORT_PATH = 'reports/evidence-selection-example.md'
STAMP = '2026-10-05 02:00:00'
QUESTION = '比较三份示例材料的证据选择方式，直接指出当前材料没有覆盖的组合，并说明依据。'
SUMMARY = ('三份示例材料分别覆盖相关性排序、不确定性排序和引用核查。'
           '当前集合没有展示两种组合：**用引用核查反馈调整证据排序**，以及'
           '**在固定检索预算下比较证据一致性**。这些是示例材料内的候选差异，不代表真实研究空白。')
REPORT = '''# 文献证据选择：方法比较与候选空白

> 界面示例：材料与结论均为人工编写，仅展示功能，不代表真实研究结果。

## 结论

三份材料分别覆盖相关性排序、不确定性排序和引用核查。当前集合尚未展示以下两种组合。

- **引用核查反馈驱动的证据排序**：把回答中的引用错误回传给排序环节，而不是只在生成后检查。
- **固定检索预算下的证据一致性比较**：在相同资料数量下比较支持、冲突与冗余，避免只比较召回数量。

这些是当前材料内的候选差异，不是已证明的新颖性结论。

## 候选空白与依据

### 引用核查反馈驱动的证据排序

示例 A 根据查询相关性选择证据，示例 B 只核查生成结果的引用。两份材料都没有把核查结果用于下一轮排序。可比较“生成后核查”和“核查反馈参与排序”是否产生不同的证据选择。

### 固定预算下比较证据一致性

示例 C 用不确定性调整排序，但没有对照冲突证据与重复证据。已有结果因此无法回答：同样读取三份资料时，一致性约束能否保留更多有效支持。

## 方法对照

| 示例材料 | 已覆盖的方法 | 当前材料未展示 |
| --- | --- | --- |
| A · 证据选择 | 查询相关性排序 | 引用核查反馈 |
| B · 引用核查 | 生成后的逐条核查 | 核查结果参与排序 |
| C · 不确定性排序 | 证据置信度调整 | 固定预算的一致性对照 |

## 证据与范围

本次展示使用三份人工材料的正文片段，没有外部检索，也没有调用模型 API。报告、分类与过程记录可在当前界面正常读取；本文仅用于说明这些交互。

## 材料与审计附录

### 资料范围

全部输入来自隔离的示例工作区。原始论文、私人会话和模型密钥不参与截图。
'''
PAPERS = [
    ('a', 'Evidence-Aware Retrieval for Scientific Question Answering', '检索与生成',
     ['相关性排序', '证据选择'], '根据查询相关性选择证据，示例未包含引用核查反馈。', 'parsed'),
    ('b', 'Citation Verification in Long-Context Scientific Summarization', '证据核查',
     ['引用核查', '长上下文'], '逐条核对回答引用，示例只覆盖生成后的检查。', 'parsed'),
    ('c', 'Uncertainty-Guided Evidence Reranking', '检索与生成',
     ['不确定性', '重排序'], '按证据置信度调整排序，示例未比较冲突与重复证据。', 'parsed'),
    ('d', 'Cross-Document Evidence Consistency', '证据核查',
     ['跨论文比较', '一致性'], '仅有示例摘要，暂不作为全文核查依据。', 'missing_fulltext'),
    ('e', 'Adaptive Literature Search under Evidence Budgets', '研究流程',
     ['自适应检索', '预算管理'], '按资料缺口选择下一步，达到预算后交付已有结果。', 'parsed'),
]


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def checked_workspace():
    """Only the marked screenshot fixture may be replaced or removed."""
    current = ROOT / 'work'
    for path in (current, WORK, WORKSPACE):
        if path.is_symlink() or (path.exists() and path.is_junction()):
            raise RuntimeError('Screenshot directory must not be a link')
    if WORKSPACE.resolve() != current.resolve() / 'readme-capture/workspace':
        raise RuntimeError('Screenshot workspace escapes the fixed test directory')
    if WORKSPACE.exists():
        if not (WORKSPACE / '.readme-fixture').is_file():
            raise RuntimeError('Refusing to replace an unmarked workspace')
        for path in WORKSPACE.rglob('*'):
            if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(WORKSPACE.resolve()):
                raise RuntimeError('Screenshot workspace contains a link')
    return WORKSPACE


def remove_fixture():
    path = checked_workspace()
    if path.exists():
        shutil.rmtree(path)


def seed():
    remove_fixture()
    from src.knowledge.library_organizer import (
        fingerprint,
        load_policy,
        organization_rows,
        snapshot,
    )
    from src.knowledge.sqlite_store import SQLiteStore
    from src.workspace.manager import WorkspaceManager
    ws = WorkspaceManager.create_workspace(WORKSPACE, 'PaperPilot 界面示例')
    (WORKSPACE / '.readme-fixture').write_text('Fictional documentation data only.\n', encoding='utf-8')
    db = SQLiteStore(WORKSPACE / 'db/ai_reader.db')
    db.init_schema()
    try:
        db.execute('INSERT INTO sessions(id,workspace_id,title,created_at,updated_at) VALUES(?,?,?,?,?)',
                   (SESSION, ws.get_workspace_id(), '证据选择：方法比较与候选空白（示例）', STAMP, STAMP))
        db.execute('INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)',
                   (MESSAGE, SESSION, 'user', QUESTION, STAMP))
        report = WORKSPACE / REPORT_PATH
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(REPORT, encoding='utf-8')
        facets = {'temporal': [], 'prior': [], 'training': [], 'mechanism': []}
        for key, title, category, tags, summary, status in PAPERS:
            pid = 'screenshot:' + key
            parsed = f'papers/parsed/{key}/full.md' if status == 'parsed' else None
            if parsed:
                path = WORKSPACE / parsed
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'# {title}\n\n> 人工编写的界面示例，非真实论文。\n\n## 方法\n\n{summary}\n', encoding='utf-8')
            db.execute('INSERT INTO papers(id,title,authors_json,year,venue,abstract,retrieval_status,parsed_markdown_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (pid, title, encode(['示例作者']), 2026, '界面示例', summary, status, parsed, STAMP, STAMP))
            db.execute('INSERT INTO paper_organization(paper_id,canonical_id,category,tags_json,summary,facets_json,run_id,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                       (pid, pid, category, encode(tags), summary, encode(facets), 'screenshot:organization', STAMP))
            if key in 'abc':
                db.execute('INSERT INTO innovation_profiles(paper_id,profile_json,extraction_model,evidence_json,confidence) VALUES(?,?,?,?,?)',
                           (pid, encode({'research_task': '文献证据选择', 'innovation_detail': summary}), '人工示例', '[]', 0.5))
                db.execute('INSERT INTO evidence_chunks(id,paper_id,source_type,section,content) VALUES(?,?,?,?,?)',
                           ('screenshot:evidence:' + key, pid, 'fulltext', '方法', summary))
        # Deliberate duplicate of one fictional record, folded with the current schema.
        db.execute("INSERT INTO papers(id,title,authors_json,year,venue,abstract,retrieval_status,parsed_markdown_path,created_at,updated_at) SELECT 'screenshot:a-copy',title,authors_json,year,venue,abstract,retrieval_status,parsed_markdown_path,created_at,updated_at FROM papers WHERE id='screenshot:a'")
        db.execute("INSERT INTO paper_organization(paper_id,canonical_id,category,tags_json,summary,facets_json,run_id,updated_at) SELECT 'screenshot:a-copy',canonical_id,category,tags_json,summary,facets_json,run_id,updated_at FROM paper_organization WHERE paper_id='screenshot:a'")
        rows, profiles = snapshot(db)
        plan = {'categories': ['检索与生成', '证据核查', '研究流程'],
                'policy_sha256': load_policy(WORKSPACE)['sha256'],
                'entries': organization_rows(db),
                'result': {'records': 6, 'papers': 5, 'categories': 3, 'merged_records': 1,
                           'suspected_groups': [], 'example': True}}
        db.execute('INSERT INTO library_organization_runs(id,state,input_hash,plan_json,before_json,applied_at) VALUES(?,?,?,?,?,?)',
                   ('screenshot:organization', 'applied', fingerprint(rows, profiles, WORKSPACE), encode(plan), '{}', STAMP))
        metadata = {'kind': 'report', 'report_path': REPORT_PATH, 'report_summary': SUMMARY,
                    'papers_found': 3, 'innovations_extracted': 3, 'missing_count': 0,
                    'gaps_count': 2, 'provisional_gaps_count': 0, 'unprocessed_count': 0,
                    'answer_status': 'resolved', 'task_type': 'gap_discovery',
                    'reading_coverage': {'completed': 3, 'admitted': 3, 'read_limit': 60}}
        db.execute('INSERT INTO messages(id,session_id,role,content,metadata_json,created_at) VALUES(?,?,?,?,?,?)',
                   ('screenshot:answer', SESSION, 'assistant', REPORT, encode(metadata), '2026-10-05 02:03:00'))
        db.execute('INSERT INTO gap_analyses(id,session_id,direction,report_path) VALUES(?,?,?,?)',
                   ('screenshot:analysis', SESSION, QUESTION, REPORT_PATH))
    finally:
        db.close()
    seed_events()


def seed_events():
    """Use the native event persistence interface; never schedule a model worker."""
    from src.runtime.tasks import native_binary
    process = subprocess.Popen([str(native_binary()), str(WORKSPACE / 'db/tasks.db')],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', creationflags=0x08000000 if os.name == 'nt' else 0)
    def rpc(op, **args):
        process.stdin.write(encode({'op': op, **args}) + '\n')
        process.stdin.flush()
        result = json.loads(process.stdout.readline())
        if not result.get('ok'):
            raise RuntimeError(result)
        return result['data']
    job_id = 'screenshot:job'
    try:
        rpc('submit', id=job_id, session_id=SESSION, request_key='screenshot:request',
            payload={'mode': 'analysis', 'session_id': SESSION, 'message_id': MESSAGE, 'message': QUESTION})
        job = rpc('claim')
        token = job['token']
        def event(agent, kind, **values):
            rpc('event', id=job_id, token=token, body={'type': kind, 'agent': agent, 'attempt_token': token, **values})
        event('DirectionParser', 'agent_started')
        event('DirectionParser', 'agent_progress', message='只比较已提供的三份示例材料，分别核对方法、范围与缺口。')
        event('DirectionParser', 'agent_completed')
        event('InnovationExtractor', 'agent_started', label='逐篇阅读与核对证据')
        for number, (key, title, _, _, summary, _) in enumerate(PAPERS[:3], 1):
            label = f'示例 {key.upper()} · ' + ('证据选择' if key == 'a' else '引用核查' if key == 'b' else '不确定性排序')
            event('InnovationExtractor', 'model_activity', call=number, phase='waiting', item_label=label)
            content = encode({'progress_summary': summary})
            for start in range(0, len(content), 24):
                event('InnovationExtractor', 'model_delta', call=number, content=content[start:start+24], item_label=label)
            event('InnovationExtractor', 'model_activity', call=number, phase='received', item_label=label)
        event('InnovationExtractor', 'agent_completed')
        event('AnalyzeAgent', 'agent_started')
        event('AnalyzeAgent', 'agent_progress', message='对照已有方法，区分材料内的缺口与尚未证明的新颖性。')
        event('AnalyzeAgent', 'agent_completed')
        event('ReportGenerator', 'agent_started')
        event('ReportGenerator', 'agent_completed')
        rpc('finish', id=job_id, token=token, status='succeeded', result={'report_path_rel': REPORT_PATH})
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default='node')
    parser.add_argument('--playwright', help='Installed Playwright module path, if not on NODE_PATH')
    args = parser.parse_args()
    # Do not inherit personal API/database credentials, including optional services.
    for name in list(os.environ):
        if name.startswith(('DEEPSEEK_', 'MINERU_', 'SILICONFLOW_', 'MYSQL_')) or name in {'OPENAI_API_KEY', 'READER_STORAGE'}:
            os.environ.pop(name)
    os.environ['READER_STORAGE'] = 'sqlite'
    seed()
    import uvicorn

    from src.app.factory import build_app
    app, _ = build_app(WORKSPACE)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                          access_log=False, log_level='warning', ws='websockets'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        for _ in range(150):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                if not thread.is_alive():
                    raise RuntimeError('Screenshot server failed to start')
                time.sleep(.1)
        else:
            raise RuntimeError('Screenshot server startup timed out')
        command = [args.node, str(ROOT / 'scripts/capture_readme.cjs'), f'http://127.0.0.1:{port}']
        if args.playwright:
            command.append(args.playwright)
        subprocess.run(command, cwd=ROOT, check=True, timeout=180)
        output = ROOT / 'docs/assets/readme'
        record = {'source': 'Current FastAPI and desktop-shared frontend; fictional local fixtures.',
                  'captured_at': datetime.now(timezone.utc).isoformat(),
                  'width': 2880, 'height': 2080, 'device_scale_factor': 2,
                  'frontend_sha256': {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in sorted((ROOT / 'src/ui/static').glob('*')) if path.is_file()},
                  'model_calls': 0, 'images': {name: {'bytes': (output / name).stat().st_size,
                  'sha256': hashlib.sha256((output / name).read_bytes()).hexdigest()}
                  for name in ('home.png', 'library.png', 'conversation.png', 'report.png', 'settings.png')}}
        (output / 'capture.json').write_text(encode(record), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=False, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        listener.close()
        if thread.is_alive():
            raise RuntimeError('Screenshot service has not stopped; fixture retained')
        remove_fixture()
        if WORK.exists() and not any(WORK.iterdir()):
            WORK.rmdir()


if __name__ == '__main__':
    main()
