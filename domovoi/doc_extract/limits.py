"""Resource limits for the document-reading worker process.

A document is somebody else's bytes and PDFium is native code, so the
worker (:mod:`domovoi.doc_extract.worker`) runs with:

* a **memory cap**: ``RLIMIT_AS`` on Linux; on Windows a job object with a
  per-process memory limit. An allocation past it fails inside the
  worker, which then dies or reports a failed read; the web process is
  untouched;
* a **CPU-time cap**: ``RLIMIT_CPU`` / the job object's per-process user
  time, on top of the wall-clock timeout the parent enforces
  (:mod:`domovoi.doc_extract.runner`).

The caps apply to the worker itself: it calls :func:`apply` first thing,
and if they can't be applied it refuses to read (fail closed).

The same pattern as ``domovoi/printing/limits.py`` (the printing branch's
drawing worker); once both have landed they can share one module.
"""

from __future__ import annotations

import sys


class LimitError(Exception):
    pass


def apply(memory_bytes: int, cpu_seconds: int) -> None:
    """Cap this process. Raises :class:`LimitError` when it can't."""
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        import resource

        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        except (ValueError, OSError) as e:
            raise LimitError("couldn't cap the reading process: %s" % e) from e
        return
    if sys.platform == "win32":
        _windows_job(memory_bytes, cpu_seconds)
        return
    raise LimitError("no way to cap a process on %s" % sys.platform)


def _windows_job(memory_bytes: int, cpu_seconds: int) -> None:
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):  # noqa: N801
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise LimitError("CreateJobObject failed (%d)" % ctypes.get_last_error())
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_PROCESS_TIME | JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_seconds) * 10_000_000   # 100 ns units
    info.ProcessMemoryLimit = int(memory_bytes)
    ok = k32.SetInformationJobObject(wintypes.HANDLE(job), JobObjectExtendedLimitInformation,
                                     ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        raise LimitError("SetInformationJobObject failed (%d)" % ctypes.get_last_error())
    if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(k32.GetCurrentProcess())):
        raise LimitError("AssignProcessToJobObject failed (%d)" % ctypes.get_last_error())
    # The handle is deliberately kept open for the life of the process
    # (closing it would end the job's limits with KILL_ON_JOB_CLOSE).
    _windows_job.handle = job  # type: ignore[attr-defined]


def worker_memory_bytes() -> int:
    """The cap handed to each worker: 2 GiB, or a third of the machine's
    memory when that is less (a small box runs at most one worker)."""
    total = _total_memory()
    cap = 2 * 1024 ** 3
    if total:
        cap = min(cap, max(512 * 1024 ** 2, total // 3))
    return int(cap)


def worker_slots() -> int:
    """How many documents may be read at once on this box: 1 on a small one
    (under 6 GiB or 4 cores), 2 otherwise."""
    import os

    cpus = os.cpu_count() or 1
    total = _total_memory() or 0
    if cpus < 4 or total < 6 * 1024 ** 3:
        return 1
    return 2


def _total_memory() -> int:
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except Exception:  # noqa: BLE001
        return 0
