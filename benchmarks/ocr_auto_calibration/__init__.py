"""OCR Auto Policy v1 evaluation harness (PR 101 — code only).

This package provides an evidence-only calibration harness for the Auto Policy
v1 (see ``aksharamd/plugins/ocr_backends/auto_selector.py``). It runs each
corpus PDF through three treatments (``tesseract``, ``unlimited_ocr``, ``auto``),
collects structured per-run metrics, computes layered preference labels, and
emits JSON + Markdown artifacts plus a human-review queue.

The harness makes NO production code changes and does NOT alter Auto Policy v1
thresholds. It only measures behaviour; policy calibration is deferred to a
later PR informed by an empirical pass on real hardware (RTX 3060 class).
"""
