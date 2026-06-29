"""Persistent savings ledger stored at ~/.aksharamd/ledger.jsonl.

Each line is a JSON object recording one compilation.
The ledger grows by append — never rewritten — so it survives crashes and is
safe to read from multiple processes.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LEDGER_DIR = Path.home() / ".aksharamd"
_LEDGER_FILE = _LEDGER_DIR / "ledger.jsonl"


@dataclass
class LedgerEntry:
    ts: str
    source: str
    file_type: str
    original_tokens: int
    optimized_tokens: int
    saved_tokens: int
    elapsed_seconds: float


def _ledger_path() -> Path:
    _LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    return _LEDGER_FILE


def append_entry(
    source: str,
    file_type: str,
    original_tokens: int,
    optimized_tokens: int,
    elapsed_seconds: float,
) -> None:
    """Append one compilation record to the ledger."""
    entry = LedgerEntry(
        ts=datetime.now(timezone.utc).isoformat(),
        source=Path(source).name,
        file_type=file_type,
        original_tokens=original_tokens,
        optimized_tokens=optimized_tokens,
        saved_tokens=max(0, original_tokens - optimized_tokens),
        elapsed_seconds=elapsed_seconds,
    )
    try:
        path = _ledger_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")
    except Exception:
        logger.debug("Could not write to ledger", exc_info=True)


def read_entries() -> list[LedgerEntry]:
    path = _ledger_path()
    if not path.exists():
        return []
    entries: list[LedgerEntry] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(LedgerEntry(**json.loads(line)))
            except Exception:
                logger.debug("Skipping malformed ledger line", exc_info=True)
    except Exception:
        logger.debug("Could not read ledger", exc_info=True)
    return entries


def get_stats() -> dict:
    entries = read_entries()
    if not entries:
        return {}

    total_saved = sum(e.saved_tokens for e in entries)
    total_original = sum(e.original_tokens for e in entries)
    total_optimized = sum(e.optimized_tokens for e in entries)
    total_elapsed = sum(e.elapsed_seconds for e in entries)

    by_type: dict[str, dict] = defaultdict(lambda: {"count": 0, "saved": 0})
    for e in entries:
        by_type[e.file_type]["count"] += 1
        by_type[e.file_type]["saved"] += e.saved_tokens

    return {
        "total_compilations": len(entries),
        "total_original_tokens": total_original,
        "total_optimized_tokens": total_optimized,
        "total_saved_tokens": total_saved,
        "reduction_percent": round((1 - total_optimized / total_original) * 100, 1) if total_original else 0.0,
        "total_elapsed_seconds": round(total_elapsed, 1),
        "by_file_type": dict(by_type),
        "recent": [asdict(e) for e in entries[-10:]],
    }
