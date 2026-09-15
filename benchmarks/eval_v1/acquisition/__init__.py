"""Offline acquisition helpers for V1 corpora.

Adapters under ``benchmarks/eval_v1/adapters/`` are strictly offline:
they consume pre-cached paths. The modules here are the *operator-side*
scripts that populate those caches and write per-document provenance
manifests. Keeping the split explicit prevents the smoke pipeline from
ever reaching out to a network endpoint at analysis time.
"""
