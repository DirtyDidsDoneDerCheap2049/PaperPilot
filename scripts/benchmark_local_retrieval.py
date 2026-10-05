"""Measure local retrieval using copied papers and the installed EXE worker.

Requires psutil in the development environment. Never copies credentials,
conversations or reports, and never starts research/embedding API requests.
The fixed test workspace must be absent before a new first-index measurement.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows readers can temporarily deny rename/delete sharing. Reporting
    # contention must not kill the resource sampler or invalidate the task.
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.05)


def bounded(path, base):
    path, base = Path(path).absolute(), Path(base).resolve()
    if not path.resolve().is_relative_to(base):
        raise ValueError('Path escapes the approved directory')
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError('Linked benchmark paths are not supported')
        if candidate == base:
            break
    return path.resolve()


def readonly(path):
    # The source must be quiescent/checkpointed. Immutable readers neither
    # create WAL/SHM files nor change read-lock bytes in the original workspace.
    wal = Path(str(path) + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError('Close the desktop and checkpoint its databases before benchmarking; live WAL is not ignored')
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    return db


class ReadOnlyDatabase:
    def __init__(self, path):
        self.conn = readonly(path)

    def fetchall(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql, params)]


def prepare(source, destination):
    from src.knowledge.paper_retrieval import digest, library_papers
    from src.knowledge.sqlite_store import SQLiteStore
    from src.workspace.manager import WorkspaceManager
    research = ReadOnlyDatabase(source / 'db/ai_reader.db')
    manifest = readonly(source / 'db/paper-retrieval/manifest.db')
    try:
        papers = library_papers(research)
        active_ids = {paper['id'] for paper in papers}
        documents = {row['paper_id']: dict(row) for row in manifest.execute('SELECT * FROM documents')
                     if row['paper_id'] in active_ids}
        expected_chunks = sum(row[0] for pid in documents for row in
                              manifest.execute('SELECT COUNT(*) FROM chunks WHERE paper_id=?', (pid,)))
        if not documents:
            raise RuntimeError('An existing verified index is required to identify the available corpus')
        WorkspaceManager.create_workspace(destination, 'isolated-local-resource-benchmark')
        store = SQLiteStore(destination / 'db/ai_reader.db')
        source_bytes = source_chars = 0
        try:
            store.init_schema()
            columns = ['id', 'title', 'authors_json', 'year', 'publication_date', 'date_source',
                       'venue', 'doi', 'arxiv_id', 'url', 'abstract', 'citation_count', 'confidence']
            for paper in papers:
                parsed = None
                if paper['id'] in documents:
                    doc = documents[paper['id']]
                    original = bounded(Path(doc['path']), source)
                    text = original.read_text('utf-8')
                    if digest(text) != doc['sha']:
                        raise RuntimeError('Source changed since its last verified index')
                    filename = hashlib.sha256(paper['id'].encode()).hexdigest()[:24] + '.md'
                    copied = destination / 'papers/parsed' / filename
                    shutil.copy2(original, copied)
                    parsed = str(copied.relative_to(destination))
                    source_bytes += original.stat().st_size
                    source_chars += len(text)
                values = [paper.get(key) for key in columns]
                values += ['parsed' if parsed else 'metadata_only', parsed]
                fields = columns + ['retrieval_status', 'parsed_markdown_path']
                store.execute(f"INSERT INTO papers({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", tuple(values))
        finally:
            store.close()
        return {'library_records': len(papers), 'fulltext_documents': len(documents),
                'expected_chunks': expected_chunks, 'source_bytes': source_bytes,
                'source_characters': source_chars, 'credentials_copied': False}
    finally:
        research.conn.close()
        manifest.close()


def serve(workspace, port):
    import uvicorn
    from src.app.factory import build_app
    from src.runtime.tasks import TaskService
    TaskService.worker_command = lambda self: [str(ROOT / 'dist/PaperPilot.exe'), '--worker', str(self.workspace)]
    app, _ = build_app(workspace)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                         log_level='warning', access_log=False))

    @app.post('/__benchmark/shutdown')
    async def shutdown():
        server.should_exit = True
        return {'ok': True}

    server.run()


def summary(values):
    if not values:
        return {'count': 0}
    ordered = sorted(values)
    def percentile(fraction):
        position = (len(ordered) - 1) * fraction
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return {'count': len(values), 'mean_ms': round(statistics.mean(values), 2),
            'p50_ms': round(percentile(.5), 2), 'p95_ms': round(percentile(.95), 2),
            'max_ms': round(max(values), 2)}


class Monitor:
    """Keep cumulative CPU for exited children; RSS sums include shared pages."""
    def __init__(self, pid, output):
        import psutil
        self.psutil = psutil
        self.root = psutil.Process(pid)
        self.cores = psutil.cpu_count()
        self.output = output
        self.phase = 'startup'
        self.rows = []
        self.totals = {}
        self.reporting_errors = 0
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        previous_time, previous_cpu = time.perf_counter(), 0
        last_written = 0
        while not self.stop.is_set():
            now = time.perf_counter()
            rss = private = 0
            children = []
            try:
                children = [self.root, *self.root.children(recursive=True)]
            except self.psutil.Error:
                pass
            for process in children:
                try:
                    identity = (process.pid, process.create_time())
                    cpu = process.cpu_times()
                    memory = process.memory_info()
                    with self.lock:
                        self.totals[identity] = cpu.user + cpu.system
                    rss += memory.rss
                    private += getattr(memory, 'private', memory.rss)
                except self.psutil.Error:
                    pass
            with self.lock:
                total = sum(self.totals.values())
                row = {'time': now, 'phase': self.phase, 'cpu_seconds': total,
                       'machine_cpu_percent': max(0, (total - previous_cpu) / max(.001, now - previous_time) / self.cores * 100),
                       'rss_bytes': rss, 'private_bytes': private, 'processes': len(children),
                       'available_bytes': self.psutil.virtual_memory().available}
                self.rows.append(row)
            previous_time, previous_cpu = now, total
            if now - last_written >= 2:
                try:
                    write_json(self.output / 'latest.json', row)
                except OSError:
                    self.reporting_errors += 1
                last_written = now
            self.stop.wait(.1)

    def begin(self, phase):
        with self.lock:
            self.phase = phase
            return time.perf_counter(), sum(self.totals.values())

    def end(self, started):
        time.sleep(.15)
        with self.lock:
            if not self.thread.is_alive() or not self.rows or time.perf_counter()-self.rows[-1]['time'] > 2:
                raise RuntimeError('Resource sampling stopped; discard this measurement and rerun')
            elapsed = time.perf_counter() - started[0]
            cpu = sum(self.totals.values()) - started[1]
            rows = [row for row in self.rows if row['phase'] == self.phase and row['time'] >= started[0]]
        return {'wall_seconds': round(elapsed, 3), 'cpu_seconds': round(cpu, 3),
                'average_machine_cpu_percent': round(cpu / elapsed / self.cores * 100, 2),
                'peak_sampled_machine_cpu_percent': round(max((r['machine_cpu_percent'] for r in rows), default=0), 2),
                'peak_rss_mib': round(max((r['rss_bytes'] for r in rows), default=0) / 2**20, 2),
                'peak_private_commit_mib': round(max((r['private_bytes'] for r in rows), default=0) / 2**20, 2),
                'minimum_system_available_gib': round(min((r['available_bytes'] for r in rows), default=0) / 2**30, 2),
                'samples': len(rows)}

    def close(self):
        self.stop.set()
        self.thread.join(5)
        with (self.output / 'samples.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]) if self.rows else [])
            writer.writeheader()
            writer.writerows(self.rows)


class Http:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, method='GET', key=None, timeout=30):
        headers = {'X-Reader-Client': 'desktop'}
        if key:
            headers['Idempotency-Key'] = key
        req = urllib.request.Request(self.base + path, method=method, headers=headers)
        started = time.perf_counter()
        with self.opener.open(req, timeout=timeout) as response:
            data = json.load(response)
        return data, (time.perf_counter() - started) * 1000


class Probes:
    def __init__(self, port):
        self.client = Http(port)
        self.rows = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        routes = ['/api/retrieval/status', '/api/papers?limit=100', '/api/jobs']
        index = 0
        while not self.stop.is_set():
            route = routes[index % len(routes)]
            started = time.perf_counter()
            try:
                _, elapsed = self.client.request(route)
                self.rows.append({'route': route, 'elapsed_ms': elapsed, 'ok': True})
            except Exception as exc:
                self.rows.append({'route': route, 'elapsed_ms': (time.perf_counter()-started)*1000,
                                  'ok': False, 'error': type(exc).__name__})
            index += 1
            self.stop.wait(.2)

    def close(self):
        self.stop.set()
        self.thread.join(35)
        return {'latency': summary([row['elapsed_ms'] for row in self.rows]),
                'failures': sum(not row['ok'] for row in self.rows),
                'routes': {route: summary([row['elapsed_ms'] for row in self.rows if row['route'] == route])
                           for route in sorted({row['route'] for row in self.rows})}}


def index_job(client, output, name):
    job, _ = client.request('/api/retrieval/index', 'POST', name)
    deadline = time.perf_counter() + 1200
    last_progress = 0
    while time.perf_counter() < deadline:
        current, _ = client.request('/api/jobs/' + job['job_id'])
        if current['status'] not in {'queued', 'running', 'cancel_requested'}:
            if current['status'] != 'succeeded':
                raise RuntimeError('Index task failed: ' + str(current.get('error')))
            return current['index_result']
        if time.perf_counter() - last_progress > 10:
            status, _ = client.request('/api/retrieval/status')
            write_json(output / 'progress.json', {'phase': name, 'documents': status['documents'],
                                                 'chunks': status['chunks'], 'job_status': current['status']})
            print(json.dumps({'phase': name, 'documents': status['documents'], 'chunks': status['chunks']}), flush=True)
            last_progress = time.perf_counter()
        time.sleep(.2)
    client.request('/api/jobs/' + job['job_id'] + '/cancel', 'POST')
    raise TimeoutError('Only the benchmark index task was cancelled at its time limit')


def measure(source, output):
    import psutil
    from scripts.update_local_desktop import sha, workspace_inventory
    source = bounded(source, ROOT / 'work')
    output = bounded(output, ROOT / 'work/testing/current')
    workspace = output / 'workspace'
    if workspace.exists():
        raise RuntimeError('Existing benchmark workspace retained; archive it before a new fresh-index run')
    output.mkdir(parents=True, exist_ok=True)
    protected = workspace_inventory(source)
    write_json(output / 'source-inventory-before.json', {str(key): value for key, value in protected.items()})
    corpus = prepare(source, workspace)
    with socket.socket() as candidate:
        candidate.bind(('127.0.0.1', 0))
        port = candidate.getsockname()[1]
    env = {key: value for key, value in os.environ.items()
           if not any(word in key.upper() for word in ('API_KEY', 'TOKEN', 'PASSWORD', 'SECRET'))}
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', READER_STORAGE='sqlite')
    command = [sys.executable, '-u', str(Path(__file__).resolve()), '--serve', str(workspace), '--port', str(port)]
    log = (output / 'server.log').open('wb')
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log,
                               creationflags=0x08000000 if os.name == 'nt' else 0)
    monitor = Monitor(process.pid, output)
    client = Http(port)
    result = {'corpus': corpus, 'logical_cpus': psutil.cpu_count(), 'physical_cpus': psutil.cpu_count(logical=False),
              'system_memory_bytes': psutil.virtual_memory().total, 'python': sys.version,
              'worker_exe': 'dist/PaperPilot.exe', 'worker_sha256': sha(ROOT / 'dist/PaperPilot.exe'),
              'sample_interval_seconds': .1, 'phases': {}, 'external_model_requests': 0,
              'scope': 'source HTTP app plus existing frozen EXE worker; no desktop WebView',
              'cpu_percent_definition': 'process-tree CPU seconds / wall seconds / logical CPUs * 100',
              'memory_definition': 'RSS process sum can count shared pages twice; private bytes are commit, not physical RAM'}
    probes = None
    try:
        ready = time.perf_counter() + 60
        while time.perf_counter() < ready:
            if process.poll() is not None:
                raise RuntimeError('Benchmark server exited; inspect the private server.log')
            try:
                client.request('/api/retrieval/status', timeout=1)
                break
            except Exception:
                time.sleep(.2)
        else:
            raise TimeoutError('Local server startup timed out')

        started = monitor.begin('idle_before')
        probes = Probes(port)
        time.sleep(6)
        result['phases']['idle_before'] = {**monitor.end(started), 'api_probes': probes.close()}
        probes = None
        for phase in ('full_index', 'incremental_index'):
            started = monitor.begin(phase)
            probes = Probes(port)
            outcome = index_job(client, output, phase)
            result['phases'][phase] = {**monitor.end(started), 'index_result': outcome, 'api_probes': probes.close()}
            probes = None
            if outcome['failed'] or outcome['external_requests'] != 0:
                raise RuntimeError('Index task returned unexpected failures or external requests')
            if outcome['index']['documents'] != corpus['fulltext_documents'] or outcome['index']['chunks'] != corpus['expected_chunks']:
                raise RuntimeError('Indexed corpus differs from the verified source corpus')
            expected = ('indexed', 'reused') if phase == 'full_index' else ('reused', 'indexed')
            if outcome[expected[0]] != corpus['fulltext_documents'] or outcome[expected[1]] != 0:
                raise RuntimeError('Fresh/incremental behavior differs from its measurement contract')
            write_json(output / 'results-partial.json', result)
        queries = ['teacher student knowledge distillation loss', 'SceneFlow EPE 实时推理速度',
                   '单目深度先验与特征融合', 'transformer attention ablation results',
                   'domain adaptation generalization', 'confidence uncertainty occlusion']
        started = monitor.begin('first_query')
        probes = Probes(port)
        data, elapsed = client.request('/api/retrieval/search?q=' + urllib.parse.quote(queries[0]))
        result['phases']['first_query'] = {**monitor.end(started), 'query_ms': round(elapsed, 2),
                                         'hits': len(data['hits']), 'api_probes': probes.close(),
                                         'note': 'First model/index load in this app process; OS file cache is warm after indexing'}
        probes = None
        started = monitor.begin('warm_queries')
        probes = Probes(port)
        durations, hits = [], []
        for i in range(30):
            data, elapsed = client.request('/api/retrieval/search?q=' + urllib.parse.quote(queries[i % len(queries)]))
            durations.append(elapsed)
            hits.append(len(data['hits']))
        result['phases']['warm_queries'] = {**monitor.end(started), 'query_latency': summary(durations),
                                          'hit_counts': hits, 'api_probes': probes.close()}
        probes = None
        started = monitor.begin('idle_after_queries')
        time.sleep(6)
        result['phases']['idle_after_queries'] = monitor.end(started)
        result['index_disk_bytes'] = sum(p.stat().st_size for p in (workspace / 'db/paper-retrieval').rglob('*') if p.is_file())
        result['model_disk_bytes'] = sum(p.stat().st_size for p in (ROOT / 'dist/_internal/resources/models/paper-embedding').rglob('*') if p.is_file())
        result['disk_free_bytes'] = shutil.disk_usage(output).free
        result['final_status'] = client.request('/api/retrieval/status')[0]
        result['sampler_reporting_errors'] = monitor.reporting_errors
    finally:
        if probes:
            probes.close()
        try:
            client.request('/__benchmark/shutdown', 'POST', timeout=10)
            process.wait(timeout=30)
        except Exception:
            if process.poll() is None:
                children = psutil.Process(process.pid).children(recursive=True)
                process.terminate()
                for child in children:
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                process.wait(timeout=10)
        monitor.close()
        log.close()
        result['index_disk_bytes_after_shutdown'] = sum(
            path.stat().st_size for path in (workspace/'db/paper-retrieval').rglob('*') if path.is_file())
        after = workspace_inventory(source)
        result['protected_source_files'] = len(protected)
        result['source_unchanged'] = after == protected
        write_json(output / 'results.json', result)
        if after != protected:
            raise RuntimeError('Daily workspace changed during the measurement; do not treat results as isolated')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'work/preview/workspace')
    parser.add_argument('--output', type=Path, default=ROOT / 'work/testing/current/local-retrieval-benchmark')
    parser.add_argument('--serve', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(bounded(args.serve, ROOT / 'work/testing/current'), args.port)
    else:
        measure(args.source, args.output)
