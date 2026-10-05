"""Initialize windowed Python streams without allocating a Windows console."""
import os
import sys


def _redirected_stream(name):
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                      wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    number = {'stdin': -10, 'stdout': -11, 'stderr': -12}[name]
    handle = kernel.GetStdHandle(number & 0xffffffff)
    # Only explicit pipe/file redirection; never attach to a terminal.
    if not handle or handle == ctypes.c_void_p(-1).value or kernel.GetFileType(handle) not in (1, 3):
        return None
    process = kernel.GetCurrentProcess()
    duplicate = wintypes.HANDLE()
    if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
        raise ctypes.WinError(ctypes.get_last_error())
    reading = name == 'stdin'
    try:
        fd = msvcrt.open_osfhandle(duplicate.value, (os.O_RDONLY if reading else os.O_WRONLY) | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(duplicate)
        raise
    try:
        return os.fdopen(fd, 'r' if reading else 'w', encoding='utf-8', errors='strict',
                         buffering=-1 if reading else 1)
    except BaseException:
        os.close(fd)
        raise


def initialize_standard_streams(*, require_pipes=False):
    """Windowed workers retain JSONL pipes; ordinary GUI launches use devnull."""
    for name in ('stdin', 'stdout', 'stderr'):
        if getattr(sys, name) is not None:
            continue
        stream = _redirected_stream(name)
        if stream is None:
            if require_pipes:
                raise RuntimeError('后台任务缺少标准输入输出管道：' + name)
            stream = open(os.devnull, 'r' if name == 'stdin' else 'w', encoding='utf-8')
        setattr(sys, name, stream)
        if getattr(sys, '__' + name + '__') is None:
            setattr(sys, '__' + name + '__', stream)
