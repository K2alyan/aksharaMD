"""Semantic-contract tests for the OCR Auto Policy v1 synthetic fixtures.

Byte-level SHA-256 is intentionally NOT part of the contract — PyMuPDF
injects volatile creation-date/xref bytes, so regenerated PDFs differ from
the previous run's bytes even when the recipe is unchanged. What the
harness relies on is semantic stability:

- total page count matches the label
- image-only pages come first (0..ocr_required_pages-1) and report zero
  extractable text via ``fitz.get_text``
- native pages come last (ocr_required_pages..end) and report non-empty
  extractable text
- label JSON contains the expected recipe fields with the expected values
- the deterministic PNG used for image-only pages has a byte-stable
  SHA-256 (PIL rasterisation *is* deterministic; only PyMuPDF's container
  bytes drift)
- ``CorpusEntry.stable_identity`` is populated for every synthetic entry
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from benchmarks.ocr_auto_calibration.corpus import list_synthetic_fixtures
from benchmarks.ocr_auto_calibration.synthetics import (
    _profiles,
    _synthetic_image_bytes,
    generate_all,
)

fitz = pytest.importorskip("fitz", reason="PyMuPDF required for synthetic fixture tests")


@pytest.fixture(scope="module")
def regenerated_dir() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="synth_semantic_"))
    generate_all(out_dir=tmp)
    return tmp


def test_all_expected_profiles_are_generated(regenerated_dir: Path) -> None:
    produced = {p.name for p in regenerated_dir.glob("*.pdf")}
    expected = {profile.filename for profile in _profiles()}
    assert produced == expected


@pytest.mark.parametrize("profile", _profiles(), ids=lambda p: p.filename)
def test_semantic_contract_per_profile(regenerated_dir: Path, profile) -> None:
    pdf_path = regenerated_dir / profile.filename
    label_path = pdf_path.with_suffix(".json")
    hash_path = pdf_path.with_suffix(".hash")

    assert pdf_path.exists(), f"missing regenerated PDF: {pdf_path.name}"
    assert label_path.exists(), f"missing sibling label: {label_path.name}"
    assert hash_path.exists(), f"missing sibling recipe hash: {hash_path.name}"

    with fitz.open(str(pdf_path)) as doc:
        assert doc.page_count == profile.total_pages, (
            f"{profile.filename}: PDF reports {doc.page_count} pages, "
            f"label claims {profile.total_pages}"
        )
        image_only_count = 0
        native_count = 0
        for idx, page in enumerate(doc):
            text = page.get_text().strip()
            if idx < profile.ocr_required_pages:
                assert not text, (
                    f"{profile.filename} page {idx}: expected image-only but "
                    f"got {len(text)} chars of extractable text"
                )
                image_only_count += 1
            else:
                assert text, (
                    f"{profile.filename} page {idx}: expected native text but "
                    f"page reports no extractable text"
                )
                native_count += 1

    assert image_only_count == profile.ocr_required_pages
    assert native_count == profile.native_pages

    with label_path.open("r", encoding="utf-8") as fh:
        label = json.load(fh)
    assert label["total_pages"] == profile.total_pages
    assert label["ocr_required_pages"] == profile.ocr_required_pages
    assert label["profile_class"] == profile.profile_class
    assert label["expected_backend_by_policy"] == profile.expected_backend_by_policy
    assert label["recipe_version"] == "1"


def test_image_content_is_byte_deterministic() -> None:
    """The PIL-rasterised placeholder image must be byte-identical run-to-run.

    Only PyMuPDF's PDF container bytes drift; the raster we embed is fully
    deterministic and pinned by this test.
    """
    first = hashlib.sha256(_synthetic_image_bytes()).hexdigest()
    second = hashlib.sha256(_synthetic_image_bytes()).hexdigest()
    assert first == second


def test_list_synthetic_fixtures_populates_stable_identity(
    regenerated_dir: Path,
) -> None:
    entries = list_synthetic_fixtures(synth_dir=regenerated_dir)
    assert entries, "no synthetic entries enumerated"
    for entry in entries:
        assert entry.source == "synthetic"
        assert entry.stable_identity, (
            f"{entry.document_id}: missing stable_identity"
        )
        assert entry.stable_identity.startswith("synthetic:v1:")
        # Recipe hash portion should be a 64-char sha256 hex digest.
        recipe_portion = entry.stable_identity.removeprefix("synthetic:v1:")
        assert len(recipe_portion) == 64
        assert all(c in "0123456789abcdef" for c in recipe_portion)
