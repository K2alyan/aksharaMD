"""Output-safety measurement + policy for OCR backends.

Provides two layers:

1. :func:`measure_repetition` — pure measurement primitive. Sliding-window
   n-gram counter over lowercased word tokens. No policy verdicts.
2. :func:`evaluate_output_safety` — Output Safety Policy v1 verdict built
   on the measurement. Returns a :class:`RepetitionSignal` that
   dispatchers use to decide whether to reject (explicit UOC) or fall
   back to Tesseract (auto).

Both callers hold the *detector version* (bumped when the measurement
algorithm changes) separately from the *policy version* (bumped when
thresholds or eligibility conditions change), so downstream code can
reason about each independently.

The calibration harness imports :func:`measure_repetition` directly and
applies its own, more-sensitive threshold, because calibration flags
patterns for human review that fall well below the runtime safety
guard's "definitely garbage" bar.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

# ── Detector primitive ────────────────────────────────────────────────

DETECTOR_VERSION = "1"
_WINDOW_WORDS = 8
_PREVIEW_MAX_CHARS = 100


def _tokenize(text: str) -> list[str]:
    """Lower-cased whitespace-delimited word tokens, punctuation stripped."""
    return re.findall(r"[A-Za-z0-9']+", text.lower())


@dataclass(frozen=True)
class RepetitionMeasurement:
    """Pure measurement — no policy interpretation.

    ``repeated_ngram_preview`` is bounded to :data:`_PREVIEW_MAX_CHARS`
    characters and never carries a raw source-text excerpt longer than
    that. The full n-gram is fingerprinted via ``repeated_ngram_sha256``
    for reviewers who need to identify duplicates across pages or docs
    without leaking content.
    """

    max_repeated_ngram_count: int
    repeated_ngram_preview: str
    repeated_ngram_sha256: str
    repetition_ratio: float
    evaluated_character_count: int
    window_words: int
    detector_version: str


def measure_repetition(
    markdown: str,
    *,
    window_words: int = _WINDOW_WORDS,
) -> RepetitionMeasurement:
    """Count the most-repeated n-gram in *markdown*.

    Pure measurement. When the text is too short to form even one
    window, every count is zero.
    """
    tokens = _tokenize(markdown)
    evaluated_character_count = len(markdown)
    if len(tokens) < window_words:
        return RepetitionMeasurement(
            max_repeated_ngram_count=0,
            repeated_ngram_preview="",
            repeated_ngram_sha256="",
            repetition_ratio=0.0,
            evaluated_character_count=evaluated_character_count,
            window_words=window_words,
            detector_version=DETECTOR_VERSION,
        )
    windows: Iterable[tuple[str, ...]] = (
        tuple(tokens[i : i + window_words])
        for i in range(len(tokens) - window_words + 1)
    )
    counter = Counter(windows)
    total_windows = len(tokens) - window_words + 1
    top_ngram, max_count = counter.most_common(1)[0]
    ngram_text = " ".join(top_ngram)
    if len(ngram_text) <= _PREVIEW_MAX_CHARS:
        preview = ngram_text
    else:
        preview = ngram_text[: _PREVIEW_MAX_CHARS - 1] + "…"
    digest = hashlib.sha256(ngram_text.encode("utf-8")).hexdigest()
    ratio = (max_count / total_windows) if total_windows > 0 else 0.0
    return RepetitionMeasurement(
        max_repeated_ngram_count=int(max_count),
        repeated_ngram_preview=preview,
        repeated_ngram_sha256=digest,
        repetition_ratio=float(ratio),
        evaluated_character_count=evaluated_character_count,
        window_words=window_words,
        detector_version=DETECTOR_VERSION,
    )


# ── Output Safety Policy v1 ───────────────────────────────────────────

UOC_OUTPUT_SAFETY_POLICY_VERSION = "1"

# The OCR Auto Policy v1 calibration harness observed max_repeated_ngram
# counts of up to 3 on real-content documents and 159–4358 on
# pathological synthetic image-only fixtures processed by the VLM
# backend. The runtime safety guard's threshold sits at 50 — a
# deliberately conservative separation from the current calibration
# observations that leaves room for moderate real-content patterns and
# moderate hallucinations to pass without rejection. Any change to this
# threshold or to the eligibility gates below must bump
# UOC_OUTPUT_SAFETY_POLICY_VERSION.
_MAX_REPEATED_NGRAM_COUNT = 50

# Short outputs cannot form enough windows to establish a meaningful
# ratio; treat them as non-signal so the guard never rejects a
# legitimately tiny page.
_MIN_EVALUATED_CHARS = 200

# Even at high absolute counts, output where the repeated phrase is a
# small share of overall text is not garbage — it is a legitimately
# recurring heading, refrain, or template row. Require the phrase to
# dominate at least 10% of the sliding windows.
_MIN_REPETITION_RATIO = 0.10


@dataclass(frozen=True)
class RepetitionSignal:
    """Output Safety Policy v1 verdict + the underlying measurement.

    All three eligibility conditions must fire together — no single
    condition alone flags an output as unsafe. ``detected`` is the
    boolean dispatchers act on.
    """

    detected: bool
    measurement: RepetitionMeasurement
    policy_version: str
    threshold_max_count: int
    threshold_min_chars: int
    threshold_min_ratio: float


def evaluate_output_safety(markdown: str) -> RepetitionSignal:
    """Output Safety Policy v1 verdict for a single OCR result markdown."""
    m = measure_repetition(markdown)
    detected = (
        m.evaluated_character_count >= _MIN_EVALUATED_CHARS
        and m.max_repeated_ngram_count >= _MAX_REPEATED_NGRAM_COUNT
        and m.repetition_ratio >= _MIN_REPETITION_RATIO
    )
    return RepetitionSignal(
        detected=detected,
        measurement=m,
        policy_version=UOC_OUTPUT_SAFETY_POLICY_VERSION,
        threshold_max_count=_MAX_REPEATED_NGRAM_COUNT,
        threshold_min_chars=_MIN_EVALUATED_CHARS,
        threshold_min_ratio=_MIN_REPETITION_RATIO,
    )
