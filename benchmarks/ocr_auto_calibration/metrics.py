"""Structural + repetition metrics derived from a compile's markdown + manifest.

All functions are pure and side-effect free so the harness can call them on
cached data during report regeneration without re-running the compiler.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

# ── Repetition detection ──────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """Lower-cased whitespace-delimited word tokens, punctuation stripped."""
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def detect_repetition(
    markdown: str,
    *,
    window_words: int = 8,
    max_repeats: int = 5,
) -> tuple[int, bool]:
    """Return ``(max_repeat_count, exceeds_threshold)`` for the markdown text.

    A sliding window of ``window_words`` tokens counts the most-frequent
    n-gram; the boolean flag is True when that count exceeds ``max_repeats``.
    Tuned to catch the UOC hallucination signature (a phrase repeated dozens
    of times) without flagging naturally recurring headings.
    """
    tokens = _tokenize(markdown)
    if len(tokens) < window_words:
        return 0, False
    windows: Iterable[tuple[str, ...]] = (
        tuple(tokens[i : i + window_words])
        for i in range(len(tokens) - window_words + 1)
    )
    counter = Counter(windows)
    if not counter:
        return 0, False
    _, max_count = counter.most_common(1)[0]
    return int(max_count), bool(max_count > max_repeats)


# ── Structural counts ─────────────────────────────────────────────────


_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s", re.MULTILINE)
_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\([^\)]+\)")
_TABLE_ROW_RE = re.compile(r"^ {0,3}\|.*\|\s*$", re.MULTILINE)


def _count_paragraphs(markdown: str) -> int:
    """Count non-empty blank-line-separated blocks that are not headings/tables."""
    if not markdown.strip():
        return 0
    blocks = [b.strip() for b in re.split(r"\n\s*\n", markdown) if b.strip()]
    para_count = 0
    for block in blocks:
        first_line = block.splitlines()[0].lstrip()
        if first_line.startswith("#"):
            continue
        if first_line.startswith("|") and first_line.endswith("|"):
            continue
        para_count += 1
    return para_count


def _count_tables(markdown: str) -> int:
    """Count GFM pipe-tables by header/separator pairs."""
    lines = markdown.splitlines()
    table_count = 0
    i = 0
    while i < len(lines) - 1:
        header = lines[i].strip()
        sep = lines[i + 1].strip()
        if (
            header.startswith("|")
            and header.endswith("|")
            and sep.startswith("|")
            and set(sep.replace("|", "").replace(":", "").strip()) <= {"-", " "}
            and "-" in sep
        ):
            table_count += 1
            # Skip past the table body to avoid double-counting.
            j = i + 2
            while j < len(lines) and _TABLE_ROW_RE.match(lines[j]):
                j += 1
            i = j
        else:
            i += 1
    return table_count


def structural_metrics(markdown: str, manifest: dict[str, Any]) -> dict[str, int]:
    """Count paragraphs, headings, image refs, tables in the markdown.

    ``manifest`` is accepted so future callers can enrich the metrics with
    manifest-derived counts (e.g. chunk count) without changing the signature.
    Today only the markdown is used.
    """
    _ = manifest  # reserved for future manifest-informed metrics
    return {
        "paragraphs": _count_paragraphs(markdown),
        "headings": len(_HEADING_RE.findall(markdown)),
        "image_refs": len(_IMAGE_REF_RE.findall(markdown)),
        "tables": _count_tables(markdown),
        "markdown_length": len(markdown),
    }


# ── Provenance completeness ───────────────────────────────────────────


def source_page_provenance_complete(
    manifest: dict[str, Any], expected_page_count: int
) -> bool:
    """True when every source page appears in the manifest's page provenance.

    Manifest schema 1.4 (post PR 100) stores ``pages`` as an integer
    total, not a per-page list. Some earlier / hypothetical schemas
    embedded ``source_pages`` as a list of dicts or ints. This helper
    tolerates both shapes; when only the integer form is present, it
    reports "cannot tell — assume complete" (returning True) because
    per-page provenance auditing lives at the document.json / block
    level, which the harness does not currently read.
    """
    if expected_page_count <= 0:
        return True
    # Legacy: some schemas embed a source-page list under either key.
    source_list = manifest.get("source_pages")
    if source_list is None:
        pages = manifest.get("pages")
        if isinstance(pages, list):
            source_list = pages
    if not isinstance(source_list, list):
        # Current schema has no per-page list; we cannot audit
        # per-page completeness from the manifest alone. Report True
        # rather than falsely flagging every doc.
        return True
    seen: set[int] = set()
    for entry in source_list:
        if isinstance(entry, dict):
            idx = entry.get("page_index")
            if idx is None:
                idx = entry.get("page")
            if isinstance(idx, int):
                seen.add(idx)
        elif isinstance(entry, int):
            seen.add(entry)
    return len(seen) >= expected_page_count
