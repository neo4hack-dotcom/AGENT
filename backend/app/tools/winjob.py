"""A Windows job object around run_python: what `resource` and the process group give it on POSIX.

Windows has no rlimits and no process groups to signal. A job object holds both at once:
a per-process memory ceiling that allocations actually hit (a `MemoryError` in the child,
where macOS silently ignores RLIMIT_AS), a CPU-time ceiling, a cap on how many processes
the code may start, and a kill that reaches every one of them — closing the job's last
handle terminates whatever is still inside.

The child is placed in the job just after it starts, while the interpreter is still
booting; code the model wrote runs only once it is inside. If the job cannot be made or
joined (an old Windows, a parent job forbidding breakaway), the run goes on under the
wall-clock watchdog alone, as before — never fails for want of a ceiling.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x0002
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x0100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
MAX_PROCESSES = 32


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.OpenProcess.restype = wintypes.HANDLE
    k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k.TerminateJobObject.restype = wintypes.BOOL
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.CloseHandle.restype = wintypes.BOOL
    return k


class Job:
    """One run's job. `kill()` ends every process in it; `close()` too, for any left behind."""

    def __init__(self, kernel32, handle) -> None:
        self._k = kernel32
        self._handle = handle

    def kill(self) -> None:
        if self._handle:
            self._k.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self._k.CloseHandle(self._handle)   # KILL_ON_JOB_CLOSE: stragglers die here
            self._handle = None


def contain(pid: int, *, memory_mb: int, cpu_s: int) -> Job | None:
    """Put process `pid` in a new job with the run's ceilings, or return None if Windows refuses."""
    try:
        k = _kernel32()
        job = k.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        basic = limits.BasicLimitInformation
        basic.LimitFlags = (_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                            | _JOB_OBJECT_LIMIT_PROCESS_MEMORY | _JOB_OBJECT_LIMIT_PROCESS_TIME)
        basic.ActiveProcessLimit = MAX_PROCESSES
        basic.PerProcessUserTimeLimit = max(1, int(cpu_s)) * 10_000_000   # 100 ns units
        limits.ProcessMemoryLimit = max(64, int(memory_mb)) * 1024 * 1024
        if not k.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                         ctypes.byref(limits), ctypes.sizeof(limits)):
            k.CloseHandle(job)
            return None
        process = k.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            k.CloseHandle(job)
            return None
        try:
            if not k.AssignProcessToJobObject(job, process):
                k.CloseHandle(job)
                return None
        finally:
            k.CloseHandle(process)
        return Job(k, job)
    except (OSError, AttributeError, ValueError):
        return None
