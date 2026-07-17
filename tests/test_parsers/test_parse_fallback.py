"""Tests for the W_PARSE_FALLBACK signal (Phase 1: detection only).

Covers:
- malformed JSON emits the warning with safe metadata,
- all-invalid JSONL emits the warning with safe metadata,
- valid JSON does NOT emit the warning,
- valid JSONL does NOT emit the warning,
- partially-invalid JSONL does NOT emit the warning in Phase 1
  (that surface will be covered by a future W_PARSE_PARTIAL),
- metadata carries the parser name, source format, exception class,
  and safe error location,
- metadata does NOT carry raw file contents (privacy invariant),
- warning maturity is "candidate",
- readiness score and quality band are unchanged in Phase 1
  (penalty stays at 0 until #41-B lands).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aksharamd.compiler import Compiler

_MALFORMED_JSON = '{"broken": '
_MALFORMED_JSON_MARKER = "broken"

_VALID_JSON_OBJECT = {"name": "test", "items": [1, 2, 3]}
_VALID_JSON = json.dumps(_VALID_JSON_OBJECT)

_VALID_JSONL = "\n".join(
    json.dumps(row) for row in [{"a": 1}, {"a": 2}, {"a": 3}]
)

_ALL_INVALID_JSONL = "\n".join(["nope", "still bad", "not json either"])
_ALL_INVALID_MARKER = "still bad"

_PARTIALLY_INVALID_JSONL = "\n".join(
    [json.dumps({"a": 1}), "not json", json.dumps({"a": 3})]
)


def _find_warning(ctx, code: str):
    return [w for w in ctx.validation.warnings if w.code == code]


def _compile(source: Path, tmp_path: Path):
    compiler = Compiler(output_dir=str(tmp_path / "out"))
    return compiler.compile(str(source))


# ── malformed JSON ────────────────────────────────────────────────────────────


def test_malformed_json_emits_parse_fallback_warning(tmp_path: Path) -> None:
    src = tmp_path / "bad.json"
    src.write_text(_MALFORMED_JSON, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    hits = _find_warning(ctx, "W_PARSE_FALLBACK")
    assert len(hits) == 1, "expected exactly one W_PARSE_FALLBACK on malformed JSON"


def test_malformed_json_metadata_contains_expected_fields(tmp_path: Path) -> None:
    src = tmp_path / "bad.json"
    src.write_text(_MALFORMED_JSON, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    (w,) = _find_warning(ctx, "W_PARSE_FALLBACK")
    md = w.metadata
    assert md["parser"] == "json_parser"
    assert md["source_format"] == "json"
    assert md["exception_class"] == "JSONDecodeError"
    assert md["error_location"].startswith("line ")
    assert "col " in md["error_location"]
    assert md["warning_maturity"] == "candidate"


def test_malformed_json_metadata_omits_raw_content(tmp_path: Path) -> None:
    """The privacy-invariant test: metadata must NEVER embed raw file contents,
    the failing snippet, the exception message string (which can include
    source text), or any user-controlled string beyond the fixed schema."""
    src = tmp_path / "bad.json"
    src.write_text(_MALFORMED_JSON, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    (w,) = _find_warning(ctx, "W_PARSE_FALLBACK")
    for key, val in w.metadata.items():
        assert _MALFORMED_JSON_MARKER not in str(val), (
            f"metadata[{key!r}] leaked source content: {val!r}"
        )
    # The message itself must not include source content either.
    assert _MALFORMED_JSON_MARKER not in w.message


def test_malformed_json_warning_maturity_is_candidate(tmp_path: Path) -> None:
    src = tmp_path / "bad.json"
    src.write_text(_MALFORMED_JSON, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    (w,) = _find_warning(ctx, "W_PARSE_FALLBACK")
    assert w.metadata["warning_maturity"] == "candidate"


# ── valid JSON (negative) ─────────────────────────────────────────────────────


def test_valid_json_does_not_emit_parse_fallback(tmp_path: Path) -> None:
    src = tmp_path / "good.json"
    src.write_text(_VALID_JSON, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    assert _find_warning(ctx, "W_PARSE_FALLBACK") == []


# ── all-invalid JSONL ─────────────────────────────────────────────────────────


def test_all_invalid_jsonl_emits_parse_fallback_warning(tmp_path: Path) -> None:
    src = tmp_path / "all_bad.jsonl"
    src.write_text(_ALL_INVALID_JSONL, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    hits = _find_warning(ctx, "W_PARSE_FALLBACK")
    assert len(hits) == 1


def test_all_invalid_jsonl_metadata_contains_counts_and_location(tmp_path: Path) -> None:
    src = tmp_path / "all_bad.jsonl"
    src.write_text(_ALL_INVALID_JSONL, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    (w,) = _find_warning(ctx, "W_PARSE_FALLBACK")
    md = w.metadata
    assert md["parser"] == "jsonl_parser"
    assert md["source_format"] == "jsonl"
    assert md["exception_class"] == "JSONDecodeError"
    # First non-empty failure is the first file line.
    assert md["error_location"].startswith("file line ")
    assert md["record_total"] == 3
    assert md["failed_record_count"] == 3
    assert md["warning_maturity"] == "candidate"


def test_all_invalid_jsonl_metadata_omits_raw_content(tmp_path: Path) -> None:
    src = tmp_path / "all_bad.jsonl"
    src.write_text(_ALL_INVALID_JSONL, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    (w,) = _find_warning(ctx, "W_PARSE_FALLBACK")
    for key, val in w.metadata.items():
        assert _ALL_INVALID_MARKER not in str(val), (
            f"metadata[{key!r}] leaked source content: {val!r}"
        )
    assert _ALL_INVALID_MARKER not in w.message


# ── valid JSONL (negative) ────────────────────────────────────────────────────


def test_valid_jsonl_does_not_emit_parse_fallback(tmp_path: Path) -> None:
    src = tmp_path / "good.jsonl"
    src.write_text(_VALID_JSONL, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    assert _find_warning(ctx, "W_PARSE_FALLBACK") == []


# ── partial JSONL (out of Phase 1 scope) ──────────────────────────────────────


def test_partial_jsonl_failure_does_not_emit_parse_fallback_in_phase_1(
    tmp_path: Path,
) -> None:
    """Partial-failure detection is intentionally NOT part of Phase 1.
    A separate W_PARSE_PARTIAL signal will address it; until then, partial
    failures must not trip W_PARSE_FALLBACK."""
    src = tmp_path / "partial.jsonl"
    src.write_text(_PARTIALLY_INVALID_JSONL, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    assert _find_warning(ctx, "W_PARSE_FALLBACK") == []


# ── readiness score / quality band unchanged in Phase 1 ───────────────────────


@pytest.mark.parametrize(
    "content,filename",
    [
        (_MALFORMED_JSON, "bad.json"),
        (_ALL_INVALID_JSONL, "all_bad.jsonl"),
    ],
)
def test_parse_fallback_does_not_change_readiness_or_band_in_phase_1(
    tmp_path: Path, content: str, filename: str
) -> None:
    """Detection-only Phase 1: emitting W_PARSE_FALLBACK must not affect
    readiness_score or quality_band. The score-effect lands in #41-B."""
    src = tmp_path / filename
    src.write_text(content, encoding="utf-8")

    ctx = _compile(src, tmp_path)

    # W_PARSE_FALLBACK is present…
    assert _find_warning(ctx, "W_PARSE_FALLBACK"), (
        f"expected W_PARSE_FALLBACK on {filename}"
    )
    m = ctx.manifest
    assert m is not None
    # …but score is unchanged from the pre-#41-A behaviour (HIGH band).
    # The exact score depends on other signals, but band must stay HIGH.
    assert m.quality_band == "HIGH", (
        f"Phase 1 must not shift the band; got {m.quality_band} for {filename}. "
        "Score-effect belongs in #41-B."
    )
    # No W_PARSE_FALLBACK-attributed deduction should appear in manifest.deductions.
    ded_rules = {d["rule_id"] for d in (m.deductions or [])}
    assert "W_PARSE_FALLBACK" not in ded_rules, (
        "Phase 1 must not add a deduction record for W_PARSE_FALLBACK"
    )
