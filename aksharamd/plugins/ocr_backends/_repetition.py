"""Shared repetition-detection value types for OCR backends.

Split out of :mod:`aksharamd.plugins.ocr_backends.output_safety` so that
both :mod:`aksharamd.plugins.ocr_backends._protocol` (which attaches a
``RepetitionSignal`` to :class:`OcrPageResult`) and ``output_safety``
(which constructs these instances via the detector algorithm) can
depend on the value types without depending on each other.

Neither ``_protocol.py`` nor ``output_safety.py`` imports the other,
either at runtime or under ``TYPE_CHECKING``. The dependency graph
is::

    _protocol.py ─────┐
                      ├──> _repetition.py
    output_safety.py ─┘

Only immutable value objects live here — no detector state, no
policy constants, no I/O, no external deps. Adding more members to
this module should preserve that property.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepetitionMeasurement:
    """Pure measurement — no policy interpretation.

    ``repeated_ngram_preview`` is bounded (max 100 chars per the
    detector's ``_PREVIEW_MAX_CHARS`` constant in
    :mod:`~aksharamd.plugins.ocr_backends.output_safety`) and never
    carries a raw source-text excerpt longer than that. The full
    n-gram is fingerprinted via ``repeated_ngram_sha256`` for
    reviewers who need to identify duplicates across pages or docs
    without leaking content.
    """

    max_repeated_ngram_count: int
    repeated_ngram_preview: str
    repeated_ngram_sha256: str
    repetition_ratio: float
    evaluated_character_count: int
    window_words: int
    detector_version: str


@dataclass(frozen=True)
class RepetitionSignal:
    """Output Safety Policy v1 verdict + the underlying measurement.

    All three eligibility conditions (count / char / ratio) must fire
    together — no single condition alone flags an output as unsafe.
    ``detected`` is the boolean dispatchers act on.
    """

    detected: bool
    measurement: RepetitionMeasurement
    policy_version: str
    threshold_max_count: int
    threshold_min_chars: int
    threshold_min_ratio: float
