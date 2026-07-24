"""Explicit ``--ocr-backend unlimited_ocr`` rejection tests.

Covers the four layers Commit 3 introduces on top of the shared detector:

1. :class:`AffectedPage` / :class:`UocOutputRepetitionError` shape.
2. :func:`collect_affected_pages` inspection helper.
3. :func:`raise_if_unsafe_uoc_result` policy trigger.
4. The dispatcher's explicit-UOC branch (``_apply_alternate_ocr_backend``)
   raising through to the CLI, which surfaces a concise structured
   error without a traceback.

The Auto branch's Tesseract fallback is deliberately out of scope here;
tests below pin that the explicit rejection does NOT invoke Tesseract
and does NOT emit an ``AUTO_OCR_BACKEND_FALLBACK`` warning. Commit 4
adds the Auto branch and its own tests.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aksharamd.plugins.ocr_backends._protocol import (
    OcrFailure,
    OcrPageResult,
)
from aksharamd.plugins.ocr_backends.output_safety import (
    UOC_OUTPUT_SAFETY_POLICY_VERSION,
    AffectedPage,
    UocOutputRepetitionError,
    collect_affected_pages,
    evaluate_output_safety,
    raise_if_unsafe_uoc_result,
)


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


def _uoc_follower(page_index: int) -> OcrPageResult:
    return OcrPageResult(
        page_index=page_index,
        markdown="",
        is_ok=True,
        meta={"aggregated_at_page_index": page_index - 1},
        repetition_signal=None,
    )


# ── Helper-level: collect + raise ─────────────────────────────────────


def test_one_unsafe_anchor_causes_rejection() -> None:
    results = [_uoc_anchor(0, _pathological_markdown())]
    with pytest.raises(UocOutputRepetitionError) as excinfo:
        raise_if_unsafe_uoc_result(results)
    err = excinfo.value
    assert err.total_affected_pages == 1
    assert err.affected_pages[0].page_index == 0
    assert err.policy_version == UOC_OUTPUT_SAFETY_POLICY_VERSION


def test_multiple_unsafe_pages_are_all_reported() -> None:
    results = [
        _uoc_anchor(0, _pathological_markdown()),
        _uoc_anchor(5, _pathological_markdown()),
        _uoc_anchor(9, _pathological_markdown()),
    ]
    with pytest.raises(UocOutputRepetitionError) as excinfo:
        raise_if_unsafe_uoc_result(results)
    err = excinfo.value
    assert err.total_affected_pages == 3
    assert [p.page_index for p in err.affected_pages] == [0, 5, 9]


def test_clean_uoc_output_does_not_raise() -> None:
    results = [
        _uoc_anchor(0, _clean_markdown()),
        _uoc_follower(1),
        _uoc_follower(2),
    ]
    # No exception; also no affected pages surfaced.
    raise_if_unsafe_uoc_result(results)
    assert collect_affected_pages(results) == []


def test_follower_pages_with_none_signal_do_not_trigger() -> None:
    """A batch with a clean anchor and follower pages whose signal is
    None must never trigger. None means "no verdict", not "unsafe"."""
    results = [
        _uoc_anchor(0, _clean_markdown()),
        _uoc_follower(1),
        _uoc_follower(2),
    ]
    raise_if_unsafe_uoc_result(results)  # does not raise


def test_detected_false_does_not_trigger() -> None:
    """A signal that was evaluated and came back detected=False must
    never trigger, even at moderate repetition counts."""
    # Craft a signal with detected=False by using clean markdown.
    clean_signal = evaluate_output_safety(_clean_markdown())
    assert clean_signal.detected is False
    results = [
        OcrPageResult(
            page_index=0,
            markdown=_clean_markdown(),
            is_ok=True,
            repetition_signal=clean_signal,
        )
    ]
    raise_if_unsafe_uoc_result(results)  # does not raise


# ── Error object: bounded evidence, structured payload ────────────────


def test_error_carries_bounded_evidence_only() -> None:
    """The error object must never carry the raw source markdown or an
    unbounded n-gram excerpt. Preview <= 100 chars, sha256 exactly 64
    hex chars."""
    with pytest.raises(UocOutputRepetitionError) as excinfo:
        raise_if_unsafe_uoc_result(
            [_uoc_anchor(0, _pathological_markdown())]
        )
    ap = excinfo.value.affected_pages[0]
    assert isinstance(ap, AffectedPage)
    assert len(ap.repeated_ngram_preview) <= 100
    assert len(ap.repeated_ngram_sha256) == 64
    assert all(c in "0123456789abcdef" for c in ap.repeated_ngram_sha256)
    # Sanity: no attribute on the error carries the raw markdown.
    for attr in vars(excinfo.value).values():
        assert _pathological_markdown() not in str(attr)


def test_error_to_structured_dict_shape_is_json_safe() -> None:
    err = UocOutputRepetitionError(
        policy_version="1",
        affected_pages=[
            AffectedPage(
                page_index=4,
                max_repeated_ngram_count=193,
                repetition_ratio=0.87,
                repeated_ngram_preview="alpha beta gamma delta epsilon zeta eta theta",
                repeated_ngram_sha256="0" * 64,
            )
        ],
    )
    payload = err.to_structured_dict()
    # Round-trippable through JSON.
    round_trip = json.loads(json.dumps(payload))
    assert round_trip["error_code"] == "UOC_OUTPUT_REPETITION"
    assert round_trip["policy_version"] == "1"
    assert round_trip["total_affected_pages"] == 1
    assert round_trip["affected_pages"][0]["page_index"] == 4
    assert round_trip["remediation"].startswith("Retry with")


def test_error_message_names_pages_and_policy_version() -> None:
    with pytest.raises(UocOutputRepetitionError) as excinfo:
        raise_if_unsafe_uoc_result(
            [
                _uoc_anchor(2, _pathological_markdown()),
                _uoc_anchor(7, _pathological_markdown()),
            ]
        )
    msg = str(excinfo.value)
    assert "page_index=2, 7" in msg
    assert f"v{UOC_OUTPUT_SAFETY_POLICY_VERSION}" in msg
    assert "Retry with" in msg


# ── Dispatcher: explicit UOC path raises; auto path does not ──────────


def _make_ctx(ocr_backend: str) -> SimpleNamespace:
    """Minimal CompilationContext-shaped stub for the dispatcher call.
    Only the attributes ``_apply_alternate_ocr_backend`` touches need
    exist."""
    warnings: list[tuple[str, str]] = []

    def _warn(code: str, msg: str) -> None:
        warnings.append((code, msg))

    return SimpleNamespace(ocr_backend=ocr_backend, warn=_warn, _warnings=warnings)


class _PathologicalUocBackend:
    """Test double: returns one anchor result with detected=True."""

    def process(self, request):
        return [_uoc_anchor(request.page_indices[0], _pathological_markdown())]


class _CleanUocBackend:
    """Test double: returns one anchor result with detected=False."""

    def process(self, request):
        return [_uoc_anchor(request.page_indices[0], _clean_markdown())]


def _run_dispatcher(ctx, backend_name: str, backend) -> None:
    from aksharamd.plugins.parsers.pdf import _apply_alternate_ocr_backend

    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=backend
    ):
        _apply_alternate_ocr_backend(
            ctx=ctx,
            pdf_path="/tmp/does_not_matter.pdf",
            ocr_source_pages=[0],
            raw_pages=[],
            all_blocks=[],
            backend_name=backend_name,
        )


def test_dispatcher_explicit_uoc_repetition_raises() -> None:
    ctx = _make_ctx("unlimited_ocr")
    with pytest.raises(UocOutputRepetitionError):
        _run_dispatcher(ctx, "unlimited_ocr", _PathologicalUocBackend())


def test_dispatcher_explicit_uoc_clean_output_does_not_raise() -> None:
    ctx = _make_ctx("unlimited_ocr")
    # Should complete without exception on clean output.
    _run_dispatcher(ctx, "unlimited_ocr", _CleanUocBackend())
    # No W_OCR_BACKEND_FAILED warning should fire on the clean path.
    assert not any(
        code == "W_OCR_BACKEND_FAILED" for code, _ in ctx._warnings
    )


def test_dispatcher_auto_selected_uoc_does_not_raise_in_commit_3() -> None:
    """Commit 3 rejects ONLY on explicit UOC. When ctx.ocr_backend is
    'auto' (the user asked auto and the policy picked UOC), the
    explicit-rejection branch must not fire — Commit 4 will add the
    Tesseract fallback for this case. Pins that Commit 3 does not
    accidentally reject the auto path."""
    ctx = _make_ctx("auto")
    _run_dispatcher(ctx, "unlimited_ocr", _PathologicalUocBackend())


def test_dispatcher_explicit_rejection_does_not_call_tesseract(
    tmp_path,
) -> None:
    """Rejection contract: on explicit-UOC failure, no Tesseract fallback
    is attempted. We verify by asserting the registry is never asked
    for the tesseract backend inside the dispatcher call."""
    ctx = _make_ctx("unlimited_ocr")
    calls: list[str] = []

    def _tracking_get_backend(name):
        calls.append(name)
        if name == "unlimited_ocr":
            return _PathologicalUocBackend()
        raise AssertionError(f"unexpected get_backend({name!r}) call")

    with patch(
        "aksharamd.plugins.ocr_backends.get_backend",
        side_effect=_tracking_get_backend,
    ):
        from aksharamd.plugins.parsers.pdf import _apply_alternate_ocr_backend

        with pytest.raises(UocOutputRepetitionError):
            _apply_alternate_ocr_backend(
                ctx=ctx,
                pdf_path=tmp_path / "doesnt_matter.pdf",
                ocr_source_pages=[0],
                raw_pages=[],
                all_blocks=[],
                backend_name="unlimited_ocr",
            )
    # get_backend was called exactly once, for unlimited_ocr — never for
    # tesseract.
    assert calls == ["unlimited_ocr"]


def test_dispatcher_rejection_does_not_emit_auto_fallback_warning() -> None:
    ctx = _make_ctx("unlimited_ocr")
    with pytest.raises(UocOutputRepetitionError):
        _run_dispatcher(ctx, "unlimited_ocr", _PathologicalUocBackend())
    codes = [code for code, _ in ctx._warnings]
    assert "AUTO_OCR_BACKEND_FALLBACK" not in codes
    assert "AUTO_OCR_BACKEND_FALLBACK_REPETITION" not in codes


def test_dispatcher_rejection_does_not_promote_partial_markdown() -> None:
    """When rejection fires, the anchor markdown must NOT reach the
    caller's block list. Verified by observing that all_blocks stays
    empty across the exception."""
    ctx = _make_ctx("unlimited_ocr")
    all_blocks: list = []
    from aksharamd.plugins.parsers.pdf import _apply_alternate_ocr_backend

    with patch(
        "aksharamd.plugins.ocr_backends.get_backend",
        return_value=_PathologicalUocBackend(),
    ):
        with pytest.raises(UocOutputRepetitionError):
            _apply_alternate_ocr_backend(
                ctx=ctx,
                pdf_path="/tmp/does_not_matter.pdf",
                ocr_source_pages=[0],
                raw_pages=[],
                all_blocks=all_blocks,
                backend_name="unlimited_ocr",
            )
    assert all_blocks == []


# ── CLI: concise structured error, no traceback ──────────────────────


def _sample_uoc_error() -> UocOutputRepetitionError:
    return UocOutputRepetitionError(
        policy_version=UOC_OUTPUT_SAFETY_POLICY_VERSION,
        affected_pages=[
            AffectedPage(
                page_index=3,
                max_repeated_ngram_count=200,
                repetition_ratio=0.5,
                repeated_ngram_preview="foo bar baz",
                repeated_ngram_sha256="a" * 64,
            )
        ],
    )


def test_cli_helper_emits_concise_structured_error(capsys) -> None:
    """The CLI's ``_emit_uoc_repetition_error`` helper renders a
    single-line error containing the error code, policy version,
    affected pages, and remediation. Never carries a Python traceback.

    Tested at the helper level rather than through the full click flow
    so machines without the UOC model installed still exercise the
    format (the availability check runs BEFORE the compile call the
    tests would otherwise need to mock)."""
    from aksharamd.cli import _emit_uoc_repetition_error

    err = _sample_uoc_error()
    _emit_uoc_repetition_error(err, output_json=False)
    captured = capsys.readouterr().out
    assert "UOC_OUTPUT_REPETITION" in captured
    assert f"v{UOC_OUTPUT_SAFETY_POLICY_VERSION}" in captured
    assert "page_index=3" in captured
    assert "Retry with" in captured
    assert "Traceback" not in captured


def test_cli_helper_json_mode_emits_structured_dict(capsys) -> None:
    """With ``output_json=True`` the helper writes a single JSON blob
    matching :meth:`UocOutputRepetitionError.to_structured_dict`, so
    downstream tooling can parse it without regex."""
    from aksharamd.cli import _emit_uoc_repetition_error

    err = _sample_uoc_error()
    _emit_uoc_repetition_error(err, output_json=True)
    captured = capsys.readouterr().out
    payload = json.loads(captured.strip().splitlines()[-1])
    assert payload["error_code"] == "UOC_OUTPUT_REPETITION"
    assert payload["policy_version"] == UOC_OUTPUT_SAFETY_POLICY_VERSION
    assert payload["total_affected_pages"] == 1
    assert payload["affected_pages"][0]["page_index"] == 3
    # No unbounded content in the payload.
    for p in payload["affected_pages"]:
        assert len(p["repeated_ngram_preview"]) <= 100
        assert len(p["repeated_ngram_sha256"]) == 64


# ── OcrFailure import is used by fixtures elsewhere in the suite; ─────
# a pin so this test file's imports stay stable if the protocol churns.
_ = OcrFailure
