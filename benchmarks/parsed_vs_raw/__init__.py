"""Parsed-vs-raw evaluation harness (Doc 2 experiment).

Measures whether AksharaMD's readiness score correlates with downstream
LLM answer quality by running a 3-arm comparison per (document, question)
pair and correlating parser-arm answer-quality against the readiness score.

See ``benchmarks/parsed_vs_raw/README.md`` for full context and usage.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
