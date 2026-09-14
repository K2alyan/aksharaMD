"""Cross-platform RSS measurement.

`resource.getrusage` is not available on Windows; use psutil where it
is installed and fall back to None otherwise. Returned units are MB.
"""
from __future__ import annotations


def current_rss_mb() -> float | None:
    try:
        import psutil  # type: ignore
        import os

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return None
