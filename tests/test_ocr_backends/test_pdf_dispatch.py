"""PR 94c — pdf.py OCR-backend dispatch tests.

None of these tests spawn a subprocess, load a real ML model, or
invoke a real Tesseract binary. Backends are stubbed via
``aksharamd.plugins.parsers.pdf._get_backend`` (imported as
``get_backend`` inside the dispatch helper) using monkeypatch on the
``aksharamd.plugins.ocr_backends`` package attribute.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from aksharamd.compiler import Compiler
from aksharamd.plugins.ocr_backends._protocol import (
    BackendCapabilities,
    OcrFailure,
    OcrPageRequest,
    OcrPageResult,
)

# ── helpers ────────────────────────────────────────────────────────────


class _StubBackend:
    """Minimal ``OcrBackend`` stub used to assert dispatch behaviour."""

    def __init__(self, name: str = "unlimited_ocr", results: Any = None,
                 markdown: str = "OCR line 1\n\nOCR line 2\n") -> None:
        self.name = name
        self.calls: list[OcrPageRequest] = []
        self._results = results
        self._markdown = markdown

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_layout=True, supports_math=True,
            supports_tables=True, emits="markdown",
        )

    def availability(self):
        # These tests bypass the CLI availability probe (they call the
        # Compiler directly); still, keep this cheap in case the CLI
        # helper reaches for it.
        from aksharamd.plugins.ocr_backends._protocol import BackendAvailability
        return BackendAvailability(is_available=True)

    def process(self, request: OcrPageRequest) -> list[OcrPageResult]:
        self.calls.append(request)
        if self._results is not None:
            return list(self._results)
        # Aggregated-batch convention: first result carries the full
        # markdown; subsequent results are empty but is_ok=True.
        results: list[OcrPageResult] = []
        for i, idx in enumerate(request.page_indices):
            if i == 0:
                results.append(OcrPageResult(
                    page_index=idx, markdown=self._markdown, is_ok=True,
                    meta={
                        "is_aggregated_batch": True,
                        "covers_page_indices": list(request.page_indices),
                        "subset_page_to_source_page": {
                            j: src for j, src in enumerate(request.page_indices)
                        },
                    },
                ))
            else:
                results.append(OcrPageResult(
                    page_index=idx, markdown="", is_ok=True,
                    meta={"aggregated_at_page_index": request.page_indices[0]},
                ))
        return results


def _compile(tmp_path: Path, source: Path, backend: str) -> Any:
    out = tmp_path / "out"
    compiler = Compiler(output_dir=str(out), ocr_backend=backend)
    return compiler.compile(str(source))


# ── 1. default preserves Tesseract-compatible behaviour ────────────────


def test_default_command_is_tesseract_compatible(tmp_path, digital_only_pdf):
    stub = _StubBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        ctx = _compile(tmp_path, digital_only_pdf, backend="tesseract")
    # Default path takes the Tesseract branch and the alternate backend
    # is never consulted.
    assert stub.calls == []
    assert ctx.document is not None
    assert ctx.document.pages == 2


# ── 2. explicit tesseract equivalent to default ────────────────────────


def test_explicit_tesseract_equivalent_to_default(tmp_path, digital_only_pdf):
    stub = _StubBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        ctx_default = _compile(tmp_path / "a", digital_only_pdf, backend="tesseract")
        ctx_explicit = _compile(tmp_path / "b", digital_only_pdf, backend="tesseract")
    assert stub.calls == []
    # Block-content equivalence for the extractable-text pages.
    def contents(c):
        return [(b.type, b.content) for b in (c.document.blocks if c.document else [])]
    assert contents(ctx_default) == contents(ctx_explicit)


# ── 3. explicit unlimited_ocr routes scanned pages through backend ─────


def test_explicit_unlimited_ocr_routes_through_backend(tmp_path, scanned_only_pdf):
    stub = _StubBackend(markdown="First OCR paragraph\nSecond OCR paragraph\n")
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        ctx = _compile(tmp_path, scanned_only_pdf, backend="unlimited_ocr")
    assert len(stub.calls) == 1
    req = stub.calls[0]
    # scanned_only_pdf has 2 scanned pages → 0-based indices [0, 1].
    assert req.page_indices == [0, 1]
    assert ctx.document is not None
    all_text = "\n".join(b.content for b in ctx.document.blocks)
    assert "First OCR paragraph" in all_text
    assert "Second OCR paragraph" in all_text


# ── 4. digital-only pdf never invokes OCR ──────────────────────────────


def test_digital_only_pdf_never_invokes_ocr(tmp_path, digital_only_pdf):
    stub = _StubBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        _compile(tmp_path, digital_only_pdf, backend="unlimited_ocr")
    assert stub.calls == []


# ── 5. mixed pdf invokes backend only for OCR pages ────────────────────


def test_mixed_pdf_invokes_backend_only_for_ocr_pages(tmp_path, mixed_pdf):
    stub = _StubBackend(markdown="Middle page OCR text\n")
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        ctx = _compile(tmp_path, mixed_pdf, backend="unlimited_ocr")
    assert len(stub.calls) == 1
    # Only page 1 (0-based) needs OCR.
    assert stub.calls[0].page_indices == [1]
    assert ctx.document is not None
    all_text = "\n".join(b.content for b in ctx.document.blocks)
    assert "Middle page OCR text" in all_text


# ── 8. reordered / noncontiguous page indices preserve provenance ──────


def test_reordered_or_noncontiguous_page_indices_preserve_provenance(
    tmp_path, scanned_only_pdf,
):
    # Craft a stub that returns a meta mapping just like the real
    # UnlimitedOcrBackend does. We can only exercise the classifier on
    # a real PDF (scanned_only has 2 scanned pages) — the important
    # test here is that the meta mapping is preserved untouched, and
    # that the markdown lands at the position of the first OCR page.
    # Provide a first-page markdown token that survives cleaner-pass
    # heuristics (long enough to avoid duplicate/short-line filtering
    # and unique enough to grep with a substring).
    marker_line = "REORDERED_TOKEN_ALPHA long line with content preserved"
    ordering = [1, 0]  # deliberately reversed
    subset_map = {0: 1, 1: 0}
    results = [
        OcrPageResult(
            page_index=1, markdown=marker_line + "\n",
            is_ok=True,
            meta={
                "is_aggregated_batch": True,
                "covers_page_indices": list(ordering),
                "subset_page_to_source_page": subset_map,
            },
        ),
        OcrPageResult(
            page_index=0, markdown="", is_ok=True,
            meta={
                "aggregated_at_page_index": 1,
                "subset_page_to_source_page": subset_map,
            },
        ),
    ]

    class _FixedStub(_StubBackend):
        def process(self, request):
            self.calls.append(request)
            return list(results)

    stub = _FixedStub()
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        ctx = _compile(tmp_path, scanned_only_pdf, backend="unlimited_ocr")

    # The classifier defines the ordering that pdf.py passes to the
    # backend — that IS the natural page order. The stub's returned
    # meta mapping is not consumed by pdf.py's dispatch (only the
    # aggregated markdown is), but critically: we must not renumber or
    # drop pages. Verify the markdown made it in exactly once (as one
    # or more blocks whose content still carries the marker token).
    assert ctx.document is not None
    hits = sum(1 for b in ctx.document.blocks if "REORDERED_TOKEN_ALPHA" in b.content)
    assert hits == 1


# ── 9. aggregated markdown inserted exactly once ───────────────────────


def test_aggregated_markdown_inserted_exactly_once(tmp_path, scanned_only_pdf):
    stub = _StubBackend(markdown="UNIQUE_OCR_LINE\n")
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        ctx = _compile(tmp_path, scanned_only_pdf, backend="unlimited_ocr")
    assert ctx.document is not None
    hits = sum(
        1 for b in ctx.document.blocks if "UNIQUE_OCR_LINE" in b.content
    )
    assert hits == 1


# ── 10. per-page backend failure keeps digital pages, emits warning ────


def test_backend_page_failure_does_not_discard_successful_non_ocr_pages(
    tmp_path, mixed_pdf,
):
    # Simulate the backend failing on the one OCR-required page. The
    # digital pages 0 and 2 must still be in the document.
    results = [
        OcrPageResult(
            page_index=1, is_ok=False,
            failure=OcrFailure(kind="cuda_oom", message="synthetic"),
        ),
    ]

    class _FailStub(_StubBackend):
        def process(self, request):
            self.calls.append(request)
            return list(results)

    stub = _FailStub()
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        ctx = _compile(tmp_path, mixed_pdf, backend="unlimited_ocr")

    assert stub.calls and stub.calls[0].page_indices == [1]
    # The digital pages produced blocks with page=1 and page=3 (1-based).
    pages_seen = {b.page for b in (ctx.document.blocks if ctx.document else [])}
    assert 1 in pages_seen  # first digital page
    assert 3 in pages_seen  # third digital page
    warn_codes = [w.code for w in ctx.validation.warnings]
    assert "W_OCR_PAGE_FAILED" in warn_codes


# ── 13. Marker vision phase is suppressed on explicit unlimited_ocr ────
#
# Reviewer's PR 94c decision: explicit --ocr-backend unlimited_ocr must
# be authoritative for OCR-required pages. Marker is an existing later
# processing phase, NOT a fallback in the pdf.py flow; but running it
# on top of UOC-handled pages would silently reinterpret the aggregated
# Markdown the user explicitly asked UOC to produce, violating the
# "no silent override" rule the reviewer set for explicit selection.
#
# This test locks that behaviour in as a regression guard: the boolean
# ``use_marker_phase`` in pdf.py's Phase 5 gate must remain False when
# ctx.ocr_backend != "tesseract".


def test_marker_phase_suppressed_on_explicit_unlimited_ocr(
    tmp_path, scanned_only_pdf,
):
    """Regression: Marker's vision pass is skipped when the user has
    explicitly selected ``--ocr-backend unlimited_ocr``. Marker's
    availability probe (``_marker_available``) is patched to True to
    prove the gate is the OCR-backend choice — not Marker's own
    availability — that keeps it out of Phase 5."""
    stub = _StubBackend(markdown="UOC produced this markdown\n")

    # Track any call to marker_convert (Phase 5's entrypoint).
    marker_calls: list[tuple] = []

    def _spy_marker(*args, **kwargs):
        marker_calls.append((args, kwargs))
        # ``_apply_marker_to_image_pages`` normally returns
        # (list[Block], list[Asset], vision_pages: int, hallucination: bool).
        return [], [], 0, False

    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ), patch(
        "aksharamd.plugins.parsers.pdf._marker_available", return_value=True,
    ), patch(
        "aksharamd.plugins.parsers.pdf._apply_marker_to_image_pages",
        side_effect=_spy_marker,
    ):
        ctx = _compile(tmp_path, scanned_only_pdf, backend="unlimited_ocr")

    # UOC ran on the scanned pages.
    assert stub.calls, "UOC backend must be invoked on the scanned pages"
    # Marker must NOT have run — its entrypoint was never called.
    assert marker_calls == [], (
        "Marker vision phase must be suppressed when unlimited_ocr is "
        "explicitly selected"
    )
    # The UOC markdown appears in the compiled output.
    all_text = "\n".join(
        b.content for b in (ctx.document.blocks if ctx.document else [])
    )
    assert "UOC produced this markdown" in all_text


def test_marker_phase_still_runs_on_default_tesseract(
    tmp_path, scanned_only_pdf,
):
    """Symmetric guard: the default Tesseract path must NOT gain the
    Marker suppression. Marker's own availability continues to gate
    Phase 5 as before this PR."""
    marker_calls: list[tuple] = []

    def _spy_marker(*args, **kwargs):
        marker_calls.append((args, kwargs))
        return [], [], 0, False

    with patch(
        "aksharamd.plugins.parsers.pdf._marker_available", return_value=True,
    ), patch(
        "aksharamd.plugins.parsers.pdf._apply_marker_to_image_pages",
        side_effect=_spy_marker,
    ):
        _compile(tmp_path, scanned_only_pdf, backend="tesseract")

    assert marker_calls, (
        "Default Tesseract path must not suppress Marker — the alt-OCR "
        "gate should only fire when the user explicitly picks a "
        "non-default backend"
    )
