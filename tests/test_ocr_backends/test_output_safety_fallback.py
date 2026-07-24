"""Auto→UOC Output Safety Policy v1 fallback tests.

Covers the four layers Commit 4 introduces:

1. Dispatcher branch — when ``--ocr-backend auto`` selected UOC and
   the UOC output tripped Policy v1, the entire UOC result is
   discarded and Tesseract runs the same request. A single
   ``AUTO_OCR_BACKEND_FALLBACK_REPETITION`` warning is emitted.
2. Structured audit payload stored on ``ctx.ocr_output_safety_audit``.
3. Manifest schema bump (1.4 → 1.5) with seven new optional fields,
   plus backward-compatible loading of older manifests.
4. Explicit-UOC behaviour from Commit 3 remains unchanged.

Fallback is intentionally document-level: no per-page mixing of
Tesseract and UOC in v1. That contract is pinned by
``test_no_uoc_blocks_reach_all_blocks_after_fallback``.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aksharamd.models.manifest import Manifest
from aksharamd.plugins.ocr_backends._protocol import (
    OcrFailure,
    OcrPageResult,
)
from aksharamd.plugins.ocr_backends.output_safety import (
    UOC_OUTPUT_SAFETY_POLICY_VERSION,
    UocOutputRepetitionError,
    evaluate_output_safety,
)

# ── Fixtures ──────────────────────────────────────────────────────────


def _pathological_markdown() -> str:
    return " ".join(["alpha beta gamma delta epsilon zeta eta theta"] * 100)


def _clean_markdown() -> str:
    return (
        "# Introduction\n\nThe system processes documents in three stages.\n"
        "Each stage takes input and produces structured output. The parser\n"
        "is streaming to keep peak memory low even on multi-hundred-page\n"
        "inputs. Rendering emits Markdown suitable for downstream\n"
        "consumption, preserving headings, tables, and image references.\n"
    ) * 20


def _uoc_anchor(page_index: int, markdown: str) -> OcrPageResult:
    return OcrPageResult(
        page_index=page_index,
        markdown=markdown,
        is_ok=True,
        meta={"is_aggregated_batch": True},
        repetition_signal=evaluate_output_safety(markdown),
    )


class _PathologicalUocBackend:
    def process(self, request):
        return [
            _uoc_anchor(request.page_indices[0], _pathological_markdown())
        ]


class _CleanUocBackend:
    def process(self, request):
        return [_uoc_anchor(request.page_indices[0], _clean_markdown())]


class _FakeTesseractBlock:
    """Minimal stand-in for a real ``aksharamd.models.Block`` — the
    dispatcher only calls ``extend`` on ``all_blocks``, so any object
    is fine."""

    def __init__(self, source_page: int, kind: str = "PARAGRAPH") -> None:
        self.source_page = source_page
        self.kind = kind


class _CleanTesseractBackend:
    """Returns clean per-page results. Recording ``process`` calls lets
    us assert that Tesseract was invoked exactly once during the
    fallback."""

    def __init__(self) -> None:
        self.process_calls = 0

    def process(self, request):
        self.process_calls += 1
        return [
            OcrPageResult(
                page_index=idx,
                blocks=[_FakeTesseractBlock(idx)],
                is_ok=True,
            )
            for idx in request.page_indices
        ]


class _FailingTesseractBackend:
    def process(self, request):
        return [
            OcrPageResult(
                page_index=idx,
                is_ok=False,
                failure=OcrFailure(kind="other", message="disk full"),
            )
            for idx in request.page_indices
        ]


def _make_ctx(ocr_backend: str) -> SimpleNamespace:
    """Minimal CompilationContext-shaped stub that satisfies the
    dispatcher. Only fields the dispatcher touches need exist."""
    warnings: list[tuple[str, str]] = []

    def _warn(code: str, msg: str, **kwargs) -> None:
        warnings.append((code, msg))

    return SimpleNamespace(
        ocr_backend=ocr_backend,
        warn=_warn,
        _warnings=warnings,
        ocr_output_safety_audit=None,
    )


def _run_dispatcher(ctx, uoc_backend, *, tesseract_backend=None) -> list:
    """Invoke ``_apply_alternate_ocr_backend`` with a controlled backend
    registry. Returns ``all_blocks`` for assertions."""
    from aksharamd.plugins.parsers.pdf import _apply_alternate_ocr_backend

    def _get(name):
        if name == "unlimited_ocr":
            return uoc_backend
        if name == "tesseract":
            if tesseract_backend is None:
                raise AssertionError(
                    "Fallback attempted but no tesseract backend supplied"
                )
            return tesseract_backend
        raise ValueError(f"unknown backend {name!r}")

    all_blocks: list = []
    with patch("aksharamd.plugins.ocr_backends.get_backend", side_effect=_get):
        _apply_alternate_ocr_backend(
            ctx=ctx,
            pdf_path="/tmp/doesnt_matter.pdf",
            ocr_source_pages=[0, 1, 2],
            raw_pages=[],
            all_blocks=all_blocks,
            backend_name="unlimited_ocr",
        )
    return all_blocks


# ── Behavioural tests ────────────────────────────────────────────────


def test_auto_uoc_clean_output_does_not_trigger_fallback() -> None:
    ctx = _make_ctx("auto")
    tesseract = _CleanTesseractBackend()
    _run_dispatcher(ctx, _CleanUocBackend(), tesseract_backend=tesseract)
    # No fallback → Tesseract never called, no audit payload, no
    # fallback warning.
    assert tesseract.process_calls == 0
    assert ctx.ocr_output_safety_audit is None
    codes = [code for code, _ in ctx._warnings]
    assert "AUTO_OCR_BACKEND_FALLBACK_REPETITION" not in codes


def test_auto_uoc_repetition_falls_back_to_tesseract_at_document_level() -> None:
    ctx = _make_ctx("auto")
    tesseract = _CleanTesseractBackend()
    blocks = _run_dispatcher(
        ctx, _PathologicalUocBackend(), tesseract_backend=tesseract
    )
    # Tesseract was called exactly once for the fallback.
    assert tesseract.process_calls == 1
    # Fallback warning emitted exactly once.
    codes = [code for code, _ in ctx._warnings]
    assert codes.count("AUTO_OCR_BACKEND_FALLBACK_REPETITION") == 1
    # Blocks came from Tesseract — the fake block type is unique.
    assert blocks and all(isinstance(b, _FakeTesseractBlock) for b in blocks)


def test_multiple_unsafe_pages_are_all_recorded() -> None:
    """When UOC returns multiple unsafe anchors, every one appears in
    the audit payload with its own bounded per-page evidence."""

    class _MultiUnsafeUoc:
        def process(self, request):
            return [
                _uoc_anchor(0, _pathological_markdown()),
                _uoc_anchor(5, _pathological_markdown()),
                _uoc_anchor(9, _pathological_markdown()),
            ]

    ctx = _make_ctx("auto")
    _run_dispatcher(ctx, _MultiUnsafeUoc(), tesseract_backend=_CleanTesseractBackend())
    audit = ctx.ocr_output_safety_audit
    assert audit["affected_page_count"] == 3
    assert [s["page_index"] for s in audit["repetition_signals"]] == [0, 5, 9]


def test_uoc_result_is_fully_discarded_on_fallback() -> None:
    """No blocks derived from the UOC anchor markdown may reach
    ``all_blocks``. Pins document-level fallback: no per-page mixing."""
    ctx = _make_ctx("auto")
    blocks = _run_dispatcher(
        ctx,
        _PathologicalUocBackend(),
        tesseract_backend=_CleanTesseractBackend(),
    )
    # Every block must originate from the fake Tesseract stub.
    assert all(isinstance(b, _FakeTesseractBlock) for b in blocks)
    # And the pathological markdown must not appear anywhere in
    # observable state — audit fields carry only bounded evidence.
    audit_str = repr(ctx.ocr_output_safety_audit)
    assert _pathological_markdown() not in audit_str


def test_final_markdown_only_from_tesseract_after_fallback() -> None:
    """Same invariant framed at the block level: after fallback,
    ``all_blocks`` contains exactly Tesseract's blocks in Tesseract's
    order."""
    ctx = _make_ctx("auto")
    tesseract = _CleanTesseractBackend()
    blocks = _run_dispatcher(
        ctx, _PathologicalUocBackend(), tesseract_backend=tesseract
    )
    # 3 requested pages × 1 stub block each.
    assert len(blocks) == 3
    assert [b.source_page for b in blocks] == [0, 1, 2]


def test_audit_payload_shape_and_bounded_evidence() -> None:
    ctx = _make_ctx("auto")
    _run_dispatcher(
        ctx, _PathologicalUocBackend(), tesseract_backend=_CleanTesseractBackend()
    )
    audit = ctx.ocr_output_safety_audit
    assert audit["output_safety_policy_version"] == UOC_OUTPUT_SAFETY_POLICY_VERSION
    assert audit["initially_selected_backend"] == "unlimited_ocr"
    assert audit["final_backend"] == "tesseract"
    assert audit["discarded_backend"] == "unlimited_ocr"
    assert audit["fallback_reason"] == "uoc_output_repetition"
    assert audit["affected_page_count"] == 1
    sig = audit["repetition_signals"][0]
    # Bounded evidence contract on every signal.
    assert len(sig["repeated_ngram_preview"]) <= 100
    assert len(sig["repeated_ngram_sha256"]) == 64
    assert isinstance(sig["evaluated_character_count"], int)


def test_explicit_uoc_still_raises_and_never_falls_back() -> None:
    """Commit 3 regression pin: ``--ocr-backend unlimited_ocr`` must
    still raise ``UocOutputRepetitionError`` and never invoke
    Tesseract. Commit 4 only touches the ``auto`` branch."""
    ctx = _make_ctx("unlimited_ocr")
    tesseract = _CleanTesseractBackend()
    with pytest.raises(UocOutputRepetitionError):
        _run_dispatcher(
            ctx, _PathologicalUocBackend(), tesseract_backend=tesseract
        )
    # Tesseract was never invoked.
    assert tesseract.process_calls == 0
    codes = [code for code, _ in ctx._warnings]
    assert "AUTO_OCR_BACKEND_FALLBACK_REPETITION" not in codes


def test_auto_initially_selecting_tesseract_is_unaffected() -> None:
    """If Auto Policy v1 initially picked Tesseract (not UOC),
    ``_apply_alternate_ocr_backend`` is not called at all in production.
    But if it WERE, the safety check must skip — the fallback branch
    is guarded on ``_resolved_backend_name == "unlimited_ocr"``.
    Simulate the corner case where backend_name is explicitly
    tesseract to be sure."""
    # Even a pathological result from a tesseract-named backend must
    # not raise or fall back (since UOC path never fired).
    class _FakeBackendClaimingTesseract:
        def process(self, request):
            return [
                _uoc_anchor(
                    request.page_indices[0], _pathological_markdown()
                )
            ]

    ctx = _make_ctx("auto")
    fake = _FakeBackendClaimingTesseract()

    def _get(name):
        return fake

    from aksharamd.plugins.parsers.pdf import _apply_alternate_ocr_backend
    all_blocks: list = []
    with patch("aksharamd.plugins.ocr_backends.get_backend", side_effect=_get):
        _apply_alternate_ocr_backend(
            ctx=ctx,
            pdf_path="/tmp/x.pdf",
            ocr_source_pages=[0],
            raw_pages=[],
            all_blocks=all_blocks,
            backend_name="tesseract",  # not unlimited_ocr → skip safety
        )
    # No fallback → no audit + no warning.
    assert ctx.ocr_output_safety_audit is None
    codes = [c for c, _ in ctx._warnings]
    assert "AUTO_OCR_BACKEND_FALLBACK_REPETITION" not in codes


def test_tesseract_failure_during_fallback_surfaces_clearly() -> None:
    """When Tesseract also fails during the safety fallback, every
    page emits a ``W_OCR_PAGE_FAILED`` warning. The UOC result stays
    discarded — the fallback path never restores unsafe UOC output."""
    ctx = _make_ctx("auto")
    blocks = _run_dispatcher(
        ctx,
        _PathologicalUocBackend(),
        tesseract_backend=_FailingTesseractBackend(),
    )
    # No blocks — Tesseract page failures mean no output.
    assert blocks == []
    # Per-page failures surfaced.
    codes = [code for code, _ in ctx._warnings]
    assert "AUTO_OCR_BACKEND_FALLBACK_REPETITION" in codes
    assert codes.count("W_OCR_PAGE_FAILED") == 3
    # Audit payload still records the discard — this WAS a fallback,
    # even if Tesseract couldn't complete it.
    assert ctx.ocr_output_safety_audit is not None
    assert ctx.ocr_output_safety_audit["discarded_backend"] == "unlimited_ocr"


def test_audit_payload_never_carries_raw_or_unbounded_ocr_text() -> None:
    """No field on the audit payload may hold raw markdown or an
    unbounded n-gram excerpt. Verified by walking every leaf value."""
    ctx = _make_ctx("auto")
    _run_dispatcher(
        ctx,
        _PathologicalUocBackend(),
        tesseract_backend=_CleanTesseractBackend(),
    )
    payload = ctx.ocr_output_safety_audit

    def _walk(v):
        if isinstance(v, dict):
            for x in v.values():
                yield from _walk(x)
        elif isinstance(v, list | tuple):
            for x in v:
                yield from _walk(x)
        elif isinstance(v, str):
            yield v

    for s in _walk(payload):
        assert _pathological_markdown() not in s
        # No single string leaf may exceed 100 chars EXCEPT sha256
        # (64 chars, safe) — but every preview is capped.
        if len(s) > 100:
            assert len(s) == 64  # sha256 fingerprint OK


# ── Manifest schema: bump + backward compat ──────────────────────────


def test_manifest_schema_version_is_bumped_to_1_5() -> None:
    m = Manifest(source="x.pdf")
    assert m.schema_version == "1.5"


def test_manifest_new_safety_fields_default_to_none() -> None:
    m = Manifest(source="x.pdf")
    assert m.ocr_output_safety_policy_version is None
    assert m.ocr_initially_selected_backend is None
    assert m.ocr_final_backend is None
    assert m.ocr_discarded_backend is None
    assert m.ocr_fallback_reason is None
    assert m.ocr_affected_page_count is None
    assert m.ocr_repetition_signals is None


def test_manifest_from_legacy_1_4_shape_still_loads() -> None:
    """A dict shaped like a pre-1.5 manifest (no safety fields, no
    schema_version override) must still round-trip through the
    Manifest model — Pydantic ignores unknown-but-optional field
    upgrades because defaults fill the gaps."""
    legacy = {
        "source": "legacy.pdf",
        "file_type": "pdf",
        "pages": 3,
        "ocr_backend_requested": "tesseract",
        "ocr_backend_selected": "tesseract",
        # NO safety fields at all.
    }
    m = Manifest(**legacy)
    # All safety fields default to None on absence.
    assert m.ocr_output_safety_policy_version is None
    assert m.ocr_initially_selected_backend is None
    assert m.ocr_final_backend is None
    assert m.ocr_discarded_backend is None
    assert m.ocr_fallback_reason is None
    assert m.ocr_affected_page_count is None
    assert m.ocr_repetition_signals is None
    # Legacy fields untouched.
    assert m.ocr_backend_requested == "tesseract"
    assert m.ocr_backend_selected == "tesseract"
    # Schema_version defaults to the current version — the legacy
    # dict just didn't override it.
    assert m.schema_version == "1.5"


def test_manifest_records_initial_discarded_and_final_backends() -> None:
    """Under fallback, the manifest simultaneously records the initial
    (pre-fallback) pick, the discarded backend, and the final effective
    backend. ``ocr_backend_selected`` also becomes the final effective
    backend (backward-compat for legacy readers)."""
    m = Manifest(
        source="x.pdf",
        ocr_backend_requested="auto",
        ocr_backend_selected="tesseract",  # final, per legacy semantics
        ocr_output_safety_policy_version=UOC_OUTPUT_SAFETY_POLICY_VERSION,
        ocr_initially_selected_backend="unlimited_ocr",
        ocr_final_backend="tesseract",
        ocr_discarded_backend="unlimited_ocr",
        ocr_fallback_reason="uoc_output_repetition",
        ocr_affected_page_count=2,
        ocr_repetition_signals=[
            {
                "page_index": 0,
                "max_repeated_ngram_count": 200,
                "repetition_ratio": 0.5,
                "evaluated_character_count": 4000,
                "repeated_ngram_preview": "alpha beta gamma delta",
                "repeated_ngram_sha256": "0" * 64,
            },
        ],
    )
    assert m.ocr_backend_requested == "auto"
    assert m.ocr_backend_selected == "tesseract"  # final effective
    assert m.ocr_initially_selected_backend == "unlimited_ocr"
    assert m.ocr_discarded_backend == "unlimited_ocr"
    assert m.ocr_final_backend == "tesseract"
    assert m.ocr_fallback_reason == "uoc_output_repetition"


# ── Backward-compat pin: OcrFailure import stays stable ──────────────
_ = OcrFailure
