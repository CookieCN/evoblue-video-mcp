"""Process-level resource measurement for the benchmark harness.

Peak RSS covers the whole process, so the harness measures *deltas*: a value
taken before provider/model load, and the high-water mark after it. Numbers are
approximate under a shared interpreter (other allocations inflate them), which
is acceptable for a gate that bounds "does the model fit a modest machine" and
documented as such in the release-gate report.
"""

import ctypes
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class MemorySample:
    """A peak-RSS reading in bytes (``None`` where the platform lacks support)."""

    peak_rss_bytes: int | None


def current_peak_rss() -> MemorySample:
    """Return the process peak RSS so far, or unknown on unsupported platforms."""
    if sys.platform == "win32":
        rss = _win32_peak_rss()
        return MemorySample(rss)
    try:
        import resource

        # ru_maxrss is KiB on Linux and bytes on macOS.
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        scale = 1024 if sys.platform == "linux" else 1
        return MemorySample(int(raw) * scale)
    except Exception:
        return MemorySample(None)


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _win32_peak_rss() -> int | None:
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Explicit signatures: the current-process pseudo-handle is 64-bit, and
        # ctypes' default int truncation makes the API fail silently.
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(_ProcessMemoryCounters),
            ctypes.c_ulong,
        )
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        process = kernel32.GetCurrentProcess()
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    except Exception:
        return None
