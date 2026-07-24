"""Content-addressable cache for the OCR Auto Policy v1 harness.

Cache key: sha256 of ``document_sha256|treatment|aksharamd_commit|
model_revision|harness_schema_version``. Entries live under
``benchmarks/ocr_auto_calibration/.cache/<key>.json``.

Invalidation is implicit: bumping the commit, model revision, or
``HARNESS_SCHEMA_VERSION`` changes the key, so old entries become
unreachable. There is no time-based expiry. Corrupted cache files are
treated as misses.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .schema import RunKey, RunResult, run_result_from_dict

_DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def cache_key_for(document_sha256: str, key: RunKey) -> str:
    """Return the sha256 hex digest used as the cache filename stem."""
    payload = (
        f"{document_sha256}|{key.treatment}|{key.aksharamd_commit}|"
        f"{key.model_revision}|{key.harness_schema_version}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def cache_path_for(
    document_sha256: str, key: RunKey, cache_dir: Path | None = None
) -> Path:
    directory = cache_dir or _DEFAULT_CACHE_DIR
    return directory / f"{cache_key_for(document_sha256, key)}.json"


def load(
    document_sha256: str, key: RunKey, *, cache_dir: Path | None = None
) -> RunResult | None:
    """Return the cached RunResult for *key* or None on miss/corruption."""
    path = cache_path_for(document_sha256, key, cache_dir=cache_dir)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload: dict[str, Any] = json.load(fh)
        return run_result_from_dict(payload)
    except (OSError, ValueError, KeyError, TypeError):
        # Corrupted or schema-mismatched cache entry — treat as a miss so the
        # harness re-runs the compile. Do not delete; the next successful
        # store overwrites it atomically.
        return None


def store(
    document_sha256: str,
    result: RunResult,
    *,
    cache_dir: Path | None = None,
) -> Path:
    """Persist *result* under its content-addressable key. Returns the path."""
    path = cache_path_for(document_sha256, result.key, cache_dir=cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2, sort_keys=True)
    # Same-filesystem replace: harness only writes into its own cache dir,
    # so os.replace's atomicity guarantee holds (no cross-volume moves).
    tmp.replace(path)
    return path
