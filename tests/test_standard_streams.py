import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from src.runtime import standard_streams


def test_gui_missing_streams_are_writable_without_a_console(monkeypatch):
    original = {name: getattr(sys, name) for name in ('stdin', 'stdout', 'stderr')}
    monkeypatch.setattr(standard_streams, '_redirected_stream', lambda name: None)
    for name in original:
        monkeypatch.setattr(sys, name, None)
    try:
        standard_streams.initialize_standard_streams()
        sys.stdout.write('中文桌面日志\n')
        sys.stderr.flush()
        assert sys.stdout.name == os.devnull
        assert sys.stderr.name == os.devnull
        assert sys.stdin.read() == ''
    finally:
        for name in original:
            getattr(sys, name).close()
            setattr(sys, name, original[name])


def test_worker_does_not_silently_discard_its_protocol(monkeypatch):
    monkeypatch.setattr(sys, 'stdin', None)
    monkeypatch.setattr(standard_streams, '_redirected_stream', lambda name: None)
    with pytest.raises(RuntimeError, match='后台任务缺少'):
        standard_streams.initialize_standard_streams(require_pipes=True)


@pytest.mark.skipif(os.name != 'nt', reason='Windows inherited-handle behavior')
def test_windowed_interpreter_recovers_unicode_pipes(tmp_path):
    executable = Path(sys.executable).with_name('pythonw.exe')
    if not executable.exists():
        pytest.skip('pythonw interpreter is unavailable')
    script = tmp_path/'stream_check.py'
    script.write_text(
        'import sys, json, ctypes\n'
        f'sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n'
        'from src.runtime.standard_streams import initialize_standard_streams\n'
        'sys.stdin = sys.stdout = sys.stderr = None\n'
        'initialize_standard_streams(require_pipes=True)\n'
        'text = sys.stdin.readline().rstrip("\\n")\n'
        'sys.stderr.write("独立诊断\\n"); sys.stderr.flush()\n'
        'print(json.dumps({"text":text,"console":bool(ctypes.windll.kernel32.GetConsoleWindow())}, ensure_ascii=False),flush=True)\n',
        encoding='utf-8')
    result = subprocess.run([str(executable), str(script)], input='中文 😀\n',
                            capture_output=True, text=True, encoding='utf-8', timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'text': '中文 😀', 'console': False}
    assert result.stderr == '独立诊断\n'


def test_frozen_desktop_is_windows_gui_executable():
    executable = os.getenv('READER_TEST_DESKTOP_EXE')
    if os.name != 'nt' or not executable:
        pytest.skip('Requires the Windows release executable')
    data = Path(executable).read_bytes()
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    assert data[offset:offset+4] == b'PE\0\0'
    # Subsystem is at the same offset in PE32 and PE32+ optional headers.
    assert struct.unpack_from('<H', data, offset+24+68)[0] == 2
