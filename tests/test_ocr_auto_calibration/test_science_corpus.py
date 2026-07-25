"""Tests for the science-corpus loader + hydrator.

Offline. Never contacts a real network. The happy-path fetch test
monkeypatches ``urllib.request.urlopen``; the network-error test
targets an unresolvable ``.invalid`` TLD (RFC 6761); a dedicated
scheme-guard test exercises the http/https allowlist.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.ocr_auto_calibration.science_corpus import (
    HydrationOutcome,
    HydrationResult,
    ScienceCorpusEntry,
    ScienceCorpusMetadata,
    _guard_cache_root,
    hydrate_science_corpus,
    load_science_corpus,
)

_LOCKFILE = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "ocr_auto_calibration"
    / "science_corpus.lock.json"
)


# ── Lockfile loader ──────────────────────────────────────────────────


def test_load_returns_five_arxiv_entries_in_lockfile_order(
    tmp_path: Path,
) -> None:
    metadata, entries = load_science_corpus(cache_root=tmp_path)
    assert isinstance(metadata, ScienceCorpusMetadata)
    assert metadata.corpus_name == "scientific-figure-complexity"
    assert len(entries) == 5
    expected_ids = [
        "attention_1706_03762_v7",
        "resnet_1512_03385_v1",
        "bert_1810_04805_v2",
        "ddpm_2006_11239_v2",
        "clip_2103_00020_v1",
    ]
    assert [e.document_id for e in entries] == expected_ids


def test_load_resolves_deterministic_cache_paths(tmp_path: Path) -> None:
    _, entries = load_science_corpus(cache_root=tmp_path)
    for entry in entries:
        # Deterministic layout: <cache_root>/<arxiv_id>_<arxiv_version>.pdf
        assert entry.pdf_path == tmp_path / f"{entry.arxiv_id}_{entry.arxiv_version}.pdf"
        assert entry.resolved is False  # nothing fetched yet


def test_load_pins_reference_fetch_only_posture() -> None:
    metadata, _ = load_science_corpus()
    posture = metadata.redistribution_posture
    assert posture.get("policy") == "reference-fetch-only"


def test_lockfile_expected_sha256_starts_as_none() -> None:
    """Phase A of the corpus lifecycle. Populated by a follow-up
    authorised edit after Commit 3 hydration runs on a trusted host."""
    _, entries = load_science_corpus()
    for entry in entries:
        assert entry.expected_sha256 is None
        assert entry.expected_size_bytes is None


# ── Cache-root guard ─────────────────────────────────────────────────


def test_guard_refuses_cache_root_inside_repo(tmp_path: Path) -> None:
    """The redistribution posture forbids writing PDFs into the repo
    tree. The guard enforces this."""
    repo_root = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="under the repo tree"):
        _guard_cache_root(repo_root / "benchmarks" / "not_a_real_cache")


def test_guard_accepts_cache_root_outside_repo(tmp_path: Path) -> None:
    _guard_cache_root(tmp_path)


def test_hydrator_refuses_cache_root_inside_repo() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    _, entries = load_science_corpus()
    with pytest.raises(ValueError):
        hydrate_science_corpus(
            entries=entries,
            cache_root=repo_root / "benchmarks" / "not_a_real_cache",
            dry_run=True,
        )


# ── Hydrator dry-run ─────────────────────────────────────────────────


def test_dry_run_reports_skipped_for_missing_files(tmp_path: Path) -> None:
    _, entries = load_science_corpus(cache_root=tmp_path)
    result = hydrate_science_corpus(
        entries=entries, cache_root=tmp_path, dry_run=True
    )
    assert isinstance(result, HydrationResult)
    assert result.dry_run is True
    assert len(result.outcomes) == 5
    for outcome in result.outcomes:
        assert outcome.status == "skipped_dry_run"


def test_dry_run_reports_already_present_when_file_exists(
    tmp_path: Path,
) -> None:
    _, entries = load_science_corpus(cache_root=tmp_path)
    entry = entries[0]
    entry.pdf_path.parent.mkdir(parents=True, exist_ok=True)
    entry.pdf_path.write_bytes(b"fake-pdf-contents")
    result = hydrate_science_corpus(
        entries=[entry], cache_root=tmp_path, dry_run=True
    )
    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.status == "already_present"
    assert outcome.observed_sha256 == hashlib.sha256(b"fake-pdf-contents").hexdigest()
    assert outcome.observed_size_bytes == len(b"fake-pdf-contents")


def test_dry_run_reports_integrity_mismatch_when_pinned_sha_disagrees(
    tmp_path: Path,
) -> None:
    _, entries = load_science_corpus(cache_root=tmp_path)
    base = entries[0]
    entry_with_wrong_pin = ScienceCorpusEntry(
        document_id=base.document_id,
        source=base.source,
        arxiv_id=base.arxiv_id,
        arxiv_version=base.arxiv_version,
        pdf_url=base.pdf_url,
        title=base.title,
        expected_page_count_hint=base.expected_page_count_hint,
        expected_complexity_class=base.expected_complexity_class,
        why_included=base.why_included,
        pdf_path=base.pdf_path,
        expected_sha256="0" * 64,
        expected_size_bytes=None,
    )
    entry_with_wrong_pin.pdf_path.parent.mkdir(parents=True, exist_ok=True)
    entry_with_wrong_pin.pdf_path.write_bytes(b"fake")
    result = hydrate_science_corpus(
        entries=[entry_with_wrong_pin], cache_root=tmp_path, dry_run=True
    )
    outcome = result.outcomes[0]
    assert outcome.status == "integrity_mismatch"
    assert outcome.observed_sha256 == hashlib.sha256(b"fake").hexdigest()


# ── Fetch happy path via monkeypatched urlopen ───────────────────────


def _override_url(base: ScienceCorpusEntry, pdf_url: str) -> ScienceCorpusEntry:
    return ScienceCorpusEntry(
        document_id=base.document_id,
        source=base.source,
        arxiv_id=base.arxiv_id,
        arxiv_version=base.arxiv_version,
        pdf_url=pdf_url,
        title=base.title,
        expected_page_count_hint=base.expected_page_count_hint,
        expected_complexity_class=base.expected_complexity_class,
        why_included=base.why_included,
        pdf_path=base.pdf_path,
        expected_sha256=None,
        expected_size_bytes=None,
    )


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._buf = payload
        self._pos = 0

    def read(self, n: int) -> bytes:
        chunk = self._buf[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_fetch_downloads_and_installs_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"%PDF-1.4\nfake-arxiv-bytes\n"

    def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr(
        "benchmarks.ocr_auto_calibration.science_corpus.urllib.request.urlopen",
        fake_urlopen,
    )

    _, entries = load_science_corpus(cache_root=tmp_path / "cache")
    entry = entries[0]  # keeps its https arxiv url; scheme guard accepts

    result = hydrate_science_corpus(
        entries=[entry], cache_root=tmp_path / "cache", dry_run=False
    )
    outcome = result.outcomes[0]
    assert outcome.status == "downloaded"
    assert entry.pdf_path.exists()
    assert entry.pdf_path.read_bytes() == payload
    # ``.part`` temp is cleaned up on success.
    assert not entry.pdf_path.with_suffix(entry.pdf_path.suffix + ".part").exists()


def test_fetch_records_network_error_without_raising(tmp_path: Path) -> None:
    _, entries = load_science_corpus(cache_root=tmp_path)
    # An HTTPS URL to the reserved ``.invalid`` TLD (RFC 6761) is
    # guaranteed to fail name resolution without hitting any real
    # host — a portable substitute for the file:// path we used
    # before the scheme guard was added.
    unreachable = _override_url(
        entries[0], "https://this-host-must-not-resolve.invalid/nothing.pdf"
    )
    result = hydrate_science_corpus(
        entries=[unreachable], cache_root=tmp_path, dry_run=False, timeout_seconds=1.0
    )
    outcome = result.outcomes[0]
    assert isinstance(outcome, HydrationOutcome)
    assert outcome.status == "network_error"
    assert outcome.error


def test_fetch_refuses_non_http_scheme(tmp_path: Path) -> None:
    """Scheme guard: a lockfile that smuggles in file:// or ftp:// or a
    custom scheme must be rejected BEFORE ``urlopen`` is called. This
    prevents SSRF-style local-file reads through
    :func:`urllib.request.urlopen`."""
    _, entries = load_science_corpus(cache_root=tmp_path)
    for bad_scheme in ("file:///etc/passwd", "ftp://example.com/x.pdf"):
        rogue = _override_url(entries[0], bad_scheme)
        result = hydrate_science_corpus(
            entries=[rogue], cache_root=tmp_path, dry_run=False, timeout_seconds=1.0
        )
        outcome = result.outcomes[0]
        assert outcome.status == "network_error"
        assert outcome.error is not None
        assert "scheme" in outcome.error


# ── Lockfile shape ───────────────────────────────────────────────────


def test_lockfile_structure_is_valid_json() -> None:
    with _LOCKFILE.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["schema_version"] == "1.0"
    assert isinstance(payload["assets"], list)
    for asset in payload["assets"]:
        assert asset["source"] == "arxiv"
        assert asset["pdf_url"].startswith("https://arxiv.org/pdf/")
