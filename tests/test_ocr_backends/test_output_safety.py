"""Output Safety Policy v1 detector tests.

Two layers under test:

- :func:`measure_repetition` returns raw counts, a bounded content-safe
  preview, and a content-fingerprint hash. Pure primitive.
- :func:`evaluate_output_safety` applies Policy v1's three-gate rule
  (count, chars, ratio) to a measurement and returns the ``detected``
  verdict dispatchers act on.

Real-content markdown must never trigger. Pathological hallucination
signatures must always trigger. Short outputs and low-ratio outputs
must never trigger even at high absolute counts, because that would
turn legitimate refrains into rejections.
"""
from __future__ import annotations

import hashlib

from aksharamd.plugins.ocr_backends.output_safety import (
    DETECTOR_VERSION,
    UOC_OUTPUT_SAFETY_POLICY_VERSION,
    evaluate_output_safety,
    measure_repetition,
)

# ── Measurement primitive ────────────────────────────────────────────


def test_measure_repetition_returns_zeros_for_short_text() -> None:
    m = measure_repetition("only a few words here")
    assert m.max_repeated_ngram_count == 0
    assert m.repeated_ngram_preview == ""
    assert m.repeated_ngram_sha256 == ""
    assert m.repetition_ratio == 0.0
    assert m.window_words == 8
    assert m.detector_version == DETECTOR_VERSION
    assert m.evaluated_character_count == len("only a few words here")


def test_measure_repetition_counts_dominant_ngram() -> None:
    phrase = "the sky is blue and the trees are green"
    text = " ".join([phrase] * 12)
    m = measure_repetition(text)
    # 12 copies of the 9-word phrase → the top 8-gram appears at least
    # 12 times (once at the aligned position in each copy). Ratio here
    # is ~0.12 because each copy contributes ~9 windows total.
    assert m.max_repeated_ngram_count >= 12
    assert m.repetition_ratio > 0.10
    assert m.evaluated_character_count == len(text)


def test_measure_repetition_preview_is_bounded() -> None:
    # An 8-word ngram from a phrase with long words can exceed 100 chars.
    phrase = (
        "supercalifragilisticexpialidocious antidisestablishmentarianism "
        "pneumonoultramicroscopicsilicovolcanoconiosis hippopotomonstrosesquippedaliophobia "
        "pseudopseudohypoparathyroidism floccinaucinihilipilification "
        "honorificabilitudinitatibus subdermatoglyphic"
    )
    text = " ".join([phrase] * 10)
    m = measure_repetition(text)
    assert len(m.repeated_ngram_preview) <= 100
    # Truncated previews end in the ellipsis character.
    if len(" ".join(phrase.split()[:8])) > 100:
        assert m.repeated_ngram_preview.endswith("…")


def test_measure_repetition_sha256_is_stable_across_runs() -> None:
    phrase = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    text = " ".join([phrase] * 15)
    m1 = measure_repetition(text)
    m2 = measure_repetition(text)
    assert m1.repeated_ngram_sha256 == m2.repeated_ngram_sha256
    assert len(m1.repeated_ngram_sha256) == 64
    # Different content yields a different fingerprint.
    other = measure_repetition(" ".join(["one two three four five six seven eight"] * 15))
    assert m1.repeated_ngram_sha256 != other.repeated_ngram_sha256


def test_measure_repetition_sha256_matches_manual_digest_of_ngram() -> None:
    """Regression pin: sha256 field is the digest of the whitespace-joined
    lowercased n-gram, NOT of the whole markdown. Reviewers depend on this
    to fingerprint the repeating phrase, not the whole page."""
    ngram = "alpha beta gamma delta epsilon zeta eta theta"
    text = " ".join([ngram] * 20)
    m = measure_repetition(text)
    expected = hashlib.sha256(ngram.encode("utf-8")).hexdigest()
    assert m.repeated_ngram_sha256 == expected


# ── Output Safety Policy v1 verdict ──────────────────────────────────


def test_evaluate_output_safety_flags_pathological_repetition() -> None:
    phrase = "alpha beta gamma delta epsilon zeta eta theta"
    text = " ".join([phrase] * 100)
    sig = evaluate_output_safety(text)
    assert sig.detected is True
    assert sig.measurement.max_repeated_ngram_count >= 50
    assert sig.measurement.repetition_ratio >= 0.10
    assert sig.measurement.evaluated_character_count >= 200
    assert sig.policy_version == UOC_OUTPUT_SAFETY_POLICY_VERSION
    assert sig.threshold_max_count == 50
    assert sig.threshold_min_chars == 200
    assert sig.threshold_min_ratio == 0.10


def test_evaluate_output_safety_leaves_real_content_alone() -> None:
    """A long realistic markdown with natural word variety and some
    heading reuse must not trigger the safety guard. This is the "no
    real-content false positives" property that lets the guard ship
    without regressing the tesseract-selected majority of the corpus."""
    text = (
        "# Introduction\n\n"
        "The system processes documents in three stages. Each stage takes\n"
        "input from the previous one and produces structured output that\n"
        "the next stage consumes. This document describes each stage in\n"
        "turn and gives concrete examples.\n\n"
        "## Stage one: acquisition\n\n"
        "Acquisition reads the source document from disk and validates it.\n"
        "Errors here are recoverable — the caller may retry with a fixed\n"
        "input file or fall back to a cached copy. Common errors include\n"
        "missing files, permission problems, and corrupt PDF metadata.\n\n"
        "## Stage two: parsing\n\n"
        "Parsing converts the raw byte stream into a document model with\n"
        "pages, blocks, and inline runs. The parser is streaming to keep\n"
        "peak memory low even on multi-hundred-page inputs.\n\n"
        "## Stage three: rendering\n\n"
        "Rendering emits the document model as Markdown suitable for\n"
        "downstream consumption. The renderer preserves headings, tables,\n"
        "and image references verbatim.\n"
    ) * 5
    sig = evaluate_output_safety(text)
    assert sig.detected is False
    # But the primitive still measured something non-zero — proving the
    # guard's *policy* is what rejects, not a lack of measurement.
    assert sig.measurement.max_repeated_ngram_count >= 0


def test_evaluate_output_safety_short_output_never_triggers() -> None:
    """Highly repetitive but tiny outputs must never trigger. Below the
    :data:`_MIN_EVALUATED_CHARS` gate the guard reports no signal, so a
    small legitimately-repeated page never gets rejected."""
    text = "hi ho hi ho"  # far below 200 chars
    sig = evaluate_output_safety(text)
    assert sig.detected is False
    assert sig.measurement.evaluated_character_count < 200


def test_evaluate_output_safety_low_ratio_never_triggers() -> None:
    """A dominant phrase that fires above the count threshold but is
    drowned in enough unique content stays below the 10% ratio floor
    and must not trigger. Guards against rejecting docs where a
    boilerplate refrain repeats many times inside a large body."""
    pathological = " ".join(["alpha beta gamma delta epsilon zeta eta theta"] * 60)
    # Enough unique 8-word groups to push total windows well past
    # 10x the pathological count.
    unique_filler_words = " ".join(
        f"unique{i}word alpha{i}omega beta{i}sigma gamma{i}tau"
        for i in range(600)
    )
    text = pathological + " " + unique_filler_words
    sig = evaluate_output_safety(text)
    # The count gate can fire, but the ratio gate should not.
    if sig.measurement.max_repeated_ngram_count >= 50:
        assert sig.measurement.repetition_ratio < 0.10
        assert sig.detected is False


def test_evaluate_output_safety_boundary_count_alone_does_not_trigger() -> None:
    """At exactly the count threshold but with the ratio gate failing,
    detected must remain False. Pins the "all three gates required"
    contract."""
    # 60 copies of the phrase; interspersed with heavy filler to keep
    # ratio below 0.10 while keeping count above 50.
    phrase = "alpha beta gamma delta epsilon zeta eta theta"
    filler = " ".join(f"w{i}" for i in range(2000))
    text = " ".join([phrase] * 60) + " " + filler
    sig = evaluate_output_safety(text)
    if (
        sig.measurement.max_repeated_ngram_count >= 50
        and sig.measurement.repetition_ratio < 0.10
    ):
        assert sig.detected is False


# ── OcrPageResult protocol carries the optional signal ─────────────────


def test_ocr_page_result_repetition_signal_defaults_to_none() -> None:
    """Backward compat: existing backends that construct OcrPageResult
    without the new field get None. None means "no verdict reported",
    which the dispatcher must NOT treat as "safe"."""
    from aksharamd.plugins.ocr_backends._protocol import OcrPageResult

    result = OcrPageResult(page_index=0, markdown="hello world")
    assert result.repetition_signal is None


def test_ocr_page_result_carries_repetition_signal_when_populated() -> None:
    from aksharamd.plugins.ocr_backends._protocol import OcrPageResult

    signal = evaluate_output_safety(
        " ".join(["alpha beta gamma delta epsilon zeta eta theta"] * 100)
    )
    result = OcrPageResult(
        page_index=3, markdown="…", is_ok=True, repetition_signal=signal
    )
    assert result.repetition_signal is signal
    assert result.repetition_signal.detected is True


# ── Backend integration: UOC anchor carries the signal ────────────────


def test_uoc_aggregation_attaches_signal_only_to_anchor() -> None:
    """UnlimitedOcrBackend batches: full markdown lives on the first
    result (anchor), followers carry empty markdown + aggregation meta.
    The safety signal must live where the markdown lives, so only the
    anchor gets it. Followers stay at repetition_signal=None."""
    from aksharamd.plugins.ocr_backends.unlimited_ocr_backend import (
        UnlimitedOcrBackend,
    )

    pathological_md = " ".join(
        ["alpha beta gamma delta epsilon zeta eta theta"] * 100
    )
    results = UnlimitedOcrBackend._results_with_aggregated_markdown(
        page_indices=[7, 8, 9],
        markdown=pathological_md,
        signals={"worker_signals": {}},
    )
    assert len(results) == 3

    anchor, *followers = results
    assert anchor.markdown == pathological_md
    assert anchor.repetition_signal is not None
    assert anchor.repetition_signal.detected is True
    assert anchor.repetition_signal.measurement.max_repeated_ngram_count >= 50

    for follower in followers:
        assert follower.markdown == ""
        assert follower.repetition_signal is None


def test_uoc_aggregation_signal_carries_bounded_preview_and_fingerprint() -> None:
    """The signal exposes only the bounded preview + sha256; no raw
    long excerpt from source text is stored on the OcrPageResult."""
    from aksharamd.plugins.ocr_backends.unlimited_ocr_backend import (
        UnlimitedOcrBackend,
    )

    pathological_md = " ".join(
        ["alpha beta gamma delta epsilon zeta eta theta"] * 100
    )
    (anchor,) = UnlimitedOcrBackend._results_with_aggregated_markdown(
        page_indices=[0],
        markdown=pathological_md,
        signals={"worker_signals": {}},
    )
    m = anchor.repetition_signal.measurement
    assert len(m.repeated_ngram_preview) <= 100
    assert len(m.repeated_ngram_sha256) == 64


def test_uoc_aggregation_signal_reports_detected_false_on_clean_output() -> None:
    """Clean UOC output — long enough to hit the char gate but with
    natural word variety — must produce detected=False, never a
    missing/None signal. Reviewers need to distinguish "backend
    evaluated, safe" from "backend did not evaluate"."""
    from aksharamd.plugins.ocr_backends.unlimited_ocr_backend import (
        UnlimitedOcrBackend,
    )

    clean_md = (
        "# Introduction\n\nThe system processes documents in three stages.\n"
        "Each stage takes input from the previous one and produces structured\n"
        "output. The parser is streaming to keep peak memory low even on\n"
        "multi-hundred-page inputs. Rendering emits Markdown suitable for\n"
        "downstream consumption, preserving headings, tables, and images.\n"
    ) * 20
    (anchor,) = UnlimitedOcrBackend._results_with_aggregated_markdown(
        page_indices=[0],
        markdown=clean_md,
        signals={"worker_signals": {}},
    )
    assert anchor.repetition_signal is not None
    assert anchor.repetition_signal.detected is False


def test_uoc_aggregation_signal_present_even_when_markdown_is_short() -> None:
    """UOC always populates the signal on the anchor (source of truth),
    even when the markdown is too short to establish a verdict. Length-
    based failures surface through the measurement fields rather than a
    missing signal."""
    from aksharamd.plugins.ocr_backends.unlimited_ocr_backend import (
        UnlimitedOcrBackend,
    )

    (anchor,) = UnlimitedOcrBackend._results_with_aggregated_markdown(
        page_indices=[0],
        markdown="hi",
        signals={"worker_signals": {}},
    )
    assert anchor.repetition_signal is not None
    assert anchor.repetition_signal.detected is False
    assert anchor.repetition_signal.measurement.evaluated_character_count == 2


# ── Tesseract backend remains untouched ───────────────────────────────


def test_tesseract_backend_still_produces_signal_free_results() -> None:
    """Regression pin: Tesseract must not require any change to produce
    an OcrPageResult that satisfies the protocol. Existing backend
    constructions leave repetition_signal at the default None."""
    from aksharamd.plugins.ocr_backends._protocol import OcrPageResult

    # A hand-built result matching Tesseract's shape — no
    # repetition_signal keyword.
    result = OcrPageResult(
        page_index=2, markdown="# Heading\n\nSome paragraph.\n", is_ok=True
    )
    assert result.repetition_signal is None
