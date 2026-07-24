"""Tests for the ``run.py`` --doc-id / --local-doc selectors.

The selectors implement a *named*, reproducible subset for shakeouts. The
important guarantees:

* --doc-id restricts to exactly the requested document_ids, in CLI order
  (order-preserving, not corpus-order-dependent).
* Unknown --doc-id values raise SystemExit with a message naming the
  offending IDs.
* --local-doc is additive; missing paths still enter as skip markers so
  the report records the intent rather than silently dropping them.
* Selection metadata is round-tripped into ``corpus_provenance`` so the
  reviewer can see exactly which docs were named.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.ocr_auto_calibration.corpus import CorpusEntry
from benchmarks.ocr_auto_calibration.run import _apply_selection


def _mk(doc_id: str, source: str = "synthetic") -> CorpusEntry:
    return CorpusEntry(
        document_id=doc_id,
        path=Path(f"/tmp/{doc_id}.pdf"),
        sha256="",
        profile_class="test",
        expected_backend_by_policy=None,
        source=source,  # type: ignore[arg-type]
    )


def test_doc_id_filter_selects_exactly_named_docs_in_cli_order() -> None:
    entries = [_mk(f"doc_{i}") for i in range(5)]
    selected, meta = _apply_selection(
        entries,
        doc_ids=["doc_3", "doc_0", "doc_4"],
        local_docs=[],
    )
    assert [e.document_id for e in selected] == ["doc_3", "doc_0", "doc_4"]
    assert meta["requested_doc_ids"] == ["doc_3", "doc_0", "doc_4"]
    assert meta["final_document_ids"] == ["doc_3", "doc_0", "doc_4"]
    assert meta["requested_local_docs"] == []
    assert meta["missing_local_doc_ids"] == []


def test_unknown_doc_id_raises_with_offending_ids_in_message() -> None:
    entries = [_mk("doc_a"), _mk("doc_b")]
    with pytest.raises(SystemExit) as excinfo:
        _apply_selection(
            entries,
            doc_ids=["doc_a", "not_a_real_id", "another_missing"],
            local_docs=[],
        )
    msg = str(excinfo.value)
    assert "not_a_real_id" in msg
    assert "another_missing" in msg
    assert "doc_a" not in msg  # a valid id should not appear in the error


def test_no_doc_ids_keeps_all_entries() -> None:
    entries = [_mk(f"doc_{i}") for i in range(3)]
    selected, meta = _apply_selection(entries, doc_ids=[], local_docs=[])
    assert [e.document_id for e in selected] == ["doc_0", "doc_1", "doc_2"]
    assert meta["requested_doc_ids"] == []
    assert meta["final_document_ids"] == ["doc_0", "doc_1", "doc_2"]


def test_local_docs_are_appended_and_missing_ones_are_flagged(
    tmp_path: Path,
) -> None:
    entries = [_mk("corpus_a")]
    real = tmp_path / "present.pdf"
    real.write_bytes(b"%PDF-1.4\n")
    missing = tmp_path / "absent.pdf"
    selected, meta = _apply_selection(
        entries,
        doc_ids=[],
        local_docs=[str(real), str(missing)],
    )
    assert [e.document_id for e in selected] == ["corpus_a", "present", "absent"]
    # Local entries carry the local source so downstream provenance is honest.
    local_entries = [e for e in selected if e.source == "local"]
    assert [e.document_id for e in local_entries] == ["present", "absent"]
    assert meta["requested_local_docs"] == [str(real), str(missing)]
    assert meta["missing_local_doc_ids"] == ["absent"]
    assert meta["final_document_ids"] == ["corpus_a", "present", "absent"]


def test_doc_id_filter_plus_local_doc_combines_named_set(tmp_path: Path) -> None:
    """The user-facing shakeout shape: N corpus IDs + 1 local doc."""
    entries = [_mk("synth_digital_only"), _mk("synth_mixed_exact_30pct"), _mk("extra")]
    local = tmp_path / "geotopo_mixed6.pdf"
    local.write_bytes(b"%PDF-1.4\n")
    selected, meta = _apply_selection(
        entries,
        doc_ids=["synth_digital_only", "synth_mixed_exact_30pct"],
        local_docs=[str(local)],
    )
    assert [e.document_id for e in selected] == [
        "synth_digital_only",
        "synth_mixed_exact_30pct",
        "geotopo_mixed6",
    ]
    # "extra" from the corpus was correctly filtered out.
    assert "extra" not in [e.document_id for e in selected]
