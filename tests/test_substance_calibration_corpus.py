"""Tests for the substance-failure calibration corpus scaffold (P0.5).

Locks the enumeration API shape and the ground-truth JSON schema. The
corpus itself is intentionally seeded with only one positive + one
negative per class for now — Phase 1/2/3 workers add more fixtures as
their detectors ship. These tests verify the scaffold works, not that
the corpus is exhaustive.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.substance_calibration.corpus import (
    FIXTURE_CLASSES,
    SubstanceCorpusEntry,
    fixture_class_dir,
    iter_substance_corpus,
    list_fixture_class,
)


# ── Scaffold presence ──────────────────────────────────────────────────────

def test_all_fixture_class_directories_exist():
    """The four fixture-class directories must be present."""
    for cls in FIXTURE_CLASSES:
        directory = fixture_class_dir(cls)
        assert directory.exists(), (
            f"Fixture class directory missing: {directory}"
        )
        assert directory.is_dir()


def test_fixture_classes_are_ordered():
    """FIXTURE_CLASSES tuple order is a public contract for iteration order."""
    assert FIXTURE_CLASSES == (
        "placeholder_stubs",
        "ocr_gibberish",
        "encoding_artifacts",
        "dropped_tables",
    )


# ── Enumeration API ────────────────────────────────────────────────────────

def test_list_fixture_class_returns_entries_for_each_seeded_class():
    """Every class ships with at least one fixture (1 positive + 1 negative)."""
    for cls in FIXTURE_CLASSES:
        entries = list_fixture_class(cls)
        assert entries, f"class {cls!r} has no fixtures"


def test_each_class_has_at_least_one_positive_and_one_negative():
    """Both bounds are needed for calibration once detectors ship."""
    for cls in FIXTURE_CLASSES:
        entries = list_fixture_class(cls)
        positives = [e for e in entries if not e.is_negative]
        negatives = [e for e in entries if e.is_negative]
        assert positives, f"class {cls!r} has no positive fixtures"
        assert negatives, f"class {cls!r} has no negative fixtures"


def test_iter_substance_corpus_yields_at_least_eight_entries():
    """4 classes x (>=1 positive + >=1 negative) = at least 8 entries."""
    entries = list(iter_substance_corpus())
    assert len(entries) >= 8, (
        f"expected >=8 seed fixtures across 4 classes, got {len(entries)}"
    )


def test_iter_substance_corpus_iterates_classes_in_order():
    """Class ordering is stable — consumers can rely on it."""
    entries = list(iter_substance_corpus())
    class_order_seen: list[str] = []
    for entry in entries:
        if not class_order_seen or class_order_seen[-1] != entry.fixture_class:
            class_order_seen.append(entry.fixture_class)
    # Every class we saw should appear in FIXTURE_CLASSES order
    expected_order = [c for c in FIXTURE_CLASSES if c in class_order_seen]
    assert class_order_seen == expected_order


# ── Fixture-entry contract ─────────────────────────────────────────────────

def test_every_entry_markdown_path_resolves():
    """Every enumerated entry points at a file that exists on disk."""
    for entry in iter_substance_corpus():
        assert entry.resolved, (
            f"{entry.document_id}: markdown_path {entry.markdown_path} not found"
        )


def test_every_entry_has_a_stable_document_id():
    ids = [e.document_id for e in iter_substance_corpus()]
    assert len(ids) == len(set(ids)), (
        f"duplicate document_ids in corpus: {[i for i in ids if ids.count(i) > 1]}"
    )
    for entry in iter_substance_corpus():
        assert entry.document_id.strip(), (
            f"blank document_id for {entry.markdown_path}"
        )


def test_positive_entries_declare_expected_rules():
    for entry in iter_substance_corpus():
        if entry.is_negative:
            continue
        assert entry.expected_rules, (
            f"positive fixture {entry.document_id} must declare expected_rules"
        )


def test_negative_entries_have_empty_expected_rules():
    for entry in iter_substance_corpus():
        if not entry.is_negative:
            continue
        assert entry.expected_rules == (), (
            f"negative fixture {entry.document_id} must have empty expected_rules"
        )


def test_every_entry_fixture_class_matches_containing_directory():
    for entry in iter_substance_corpus():
        assert entry.markdown_path.parent.name == entry.fixture_class, (
            f"{entry.document_id}: fixture_class {entry.fixture_class!r} "
            f"does not match parent dir {entry.markdown_path.parent.name!r}"
        )


# ── Ground-truth JSON schema conformance ───────────────────────────────────

def test_every_markdown_has_a_ground_truth_json():
    """Enumeration silently skips MDs without JSON siblings — this test
    catches accidentally-orphaned MDs before they slip into a PR."""
    for cls in FIXTURE_CLASSES:
        directory = fixture_class_dir(cls)
        for md_path in directory.glob("*.md"):
            label_path = md_path.with_suffix(".json")
            assert label_path.exists(), (
                f"{md_path} has no sibling ground-truth JSON"
            )


def test_every_ground_truth_json_parses():
    for cls in FIXTURE_CLASSES:
        directory = fixture_class_dir(cls)
        for label_path in directory.glob("*.json"):
            with label_path.open("r", encoding="utf-8") as fh:
                try:
                    payload = json.load(fh)
                except json.JSONDecodeError as exc:
                    pytest.fail(f"{label_path}: invalid JSON: {exc}")
            required = {"document_id", "fixture_class", "is_negative",
                        "expected_rules", "expected_pages", "notes"}
            missing = required - payload.keys()
            assert not missing, (
                f"{label_path} missing required fields: {sorted(missing)}"
            )


def test_ground_truth_fixture_class_matches_directory():
    """Sanity: the label's fixture_class must equal its containing directory."""
    for cls in FIXTURE_CLASSES:
        directory = fixture_class_dir(cls)
        for label_path in directory.glob("*.json"):
            with label_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            assert payload["fixture_class"] == cls, (
                f"{label_path}: fixture_class {payload['fixture_class']!r} "
                f"does not match directory {cls!r}"
            )


# ── SubstanceCorpusEntry dataclass invariants ──────────────────────────────

def test_entry_is_frozen_and_hashable():
    """CorpusEntry is used as a dict key downstream — must be hashable."""
    entry = next(iter(iter_substance_corpus()))
    with pytest.raises(Exception):
        entry.document_id = "changed"  # type: ignore[misc]


def test_entry_source_pdf_path_is_none_when_absent():
    """Most fixtures ship without a source PDF; only Phase 3 needs one."""
    for entry in iter_substance_corpus():
        if entry.source_pdf_path is not None:
            assert entry.source_pdf_path.exists(), (
                f"{entry.document_id}: source_pdf declared but file missing"
            )


def test_entry_expected_pages_are_positive_integers():
    for entry in iter_substance_corpus():
        for page in entry.expected_pages:
            assert isinstance(page, int) and page >= 1, (
                f"{entry.document_id}: page {page} not a positive int"
            )
