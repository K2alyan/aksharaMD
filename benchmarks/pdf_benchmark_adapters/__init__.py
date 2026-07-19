"""Benchmark adapters for PDF Benchmark v1 (Issue #68).

One module per competitor. Every adapter consumes the frozen manifest
in ``benchmarks/pdf_benchmark_v1_manifest.json`` and produces:

- a per-file result carrying execution / content / structural verdicts,
- a slice-aggregated report,
- a Markdown summary.

Adapters MUST NOT reuse AksharaMD readiness scores or warning codes.
Every metric they compute is tool-neutral. Where a competitor lacks a
signal AksharaMD emits (e.g., ``NEAR_EMPTY_OUTPUT``), the adapter
substitutes an equivalent purely-mechanical rule (e.g., "output has
fewer than N non-whitespace characters") and documents the substitution
in its evaluation-semantics note.
"""
