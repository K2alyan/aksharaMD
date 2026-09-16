"""Stdout / stderr capture per invocation.

Adapters may print. The smoke redirects each invocation's stdout and
stderr to per-invocation files and records the SHA-256 + byte length
of what was captured. Stream contents are retained on disk but never
displayed on the reviewer surface (they can leak parser identity via
banner strings).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CapturedStream:
    path: Path
    byte_length: int
    sha256: str


def _write_stream(path: Path, content: str) -> CapturedStream:
    data = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return CapturedStream(
        path=path,
        byte_length=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def write_stdout(path: Path, content: str) -> CapturedStream:
    return _write_stream(path, content)


def write_stderr(path: Path, content: str) -> CapturedStream:
    return _write_stream(path, content)
