"""Run the layout-complexity evidence pipeline end-to-end (Commit 3).

Wires the science-corpus loader, hydrator, capture module, analysis,
and report writer together. Evidence only: does not compile documents,
does not touch the manifest, and does not choose an OCR backend.

Usage
-----

Dry run (default): resolves corpus, reports what would be fetched,
runs capture on any already-present PDFs, writes the report. Safe on
a machine without network::

    python -m benchmarks.ocr_auto_calibration.run_layout_complexity_evidence \
        --out benchmarks/ocr_auto_calibration/results/layout_complexity_v1

Full hydration (network + disk in the per-user cache root outside the
repo)::

    python -m benchmarks.ocr_auto_calibration.run_layout_complexity_evidence \
        --hydrate --out ...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmarks.ocr_auto_calibration.layout_complexity_analysis import analyze
from benchmarks.ocr_auto_calibration.layout_complexity_capture import (
    LayoutComplexityCapture,
    capture_pdf,
)
from benchmarks.ocr_auto_calibration.layout_complexity_report import (
    write_analysis_json,
    write_capture_json,
    write_markdown_report,
)
from benchmarks.ocr_auto_calibration.science_corpus import (
    HydrationResult,
    ScienceCorpusEntry,
    hydrate_science_corpus,
    load_science_corpus,
)

_DEFAULT_OUT = (
    Path(__file__).resolve().parent / "results" / "layout_complexity_v1"
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Layout Complexity v1 evidence harness against the "
            "science corpus. Evidence only — no production routing or "
            "manifest side effects."
        )
    )
    parser.add_argument(
        "--hydrate",
        action="store_true",
        help=(
            "Fetch missing PDFs from arxiv into the per-user cache root "
            "outside the repo. Without this flag the harness runs in "
            "dry-run mode against whatever PDFs are already present."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help="Output directory for capture.json / analysis.json / REPORT.md",
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=None,
        help=(
            "Override the per-user cache root. Defaults to "
            "$AKSHARAMD_SCIENCE_CORPUS_CACHE or a per-host default. "
            "Must be OUTSIDE the repo tree."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-asset fetch timeout in seconds (default 30).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    metadata, entries = load_science_corpus(cache_root=args.cache_root)
    print(
        f"[info] loaded science corpus '{metadata.corpus_name}' "
        f"({len(entries)} asset(s)); phase={metadata.phase}",
        file=sys.stderr,
    )

    hydration = hydrate_science_corpus(
        entries=entries,
        cache_root=args.cache_root,
        dry_run=not args.hydrate,
        timeout_seconds=args.timeout,
    )
    _log_hydration(hydration)

    resolved_entries = [e for e in entries if e.pdf_path.exists()]
    if not resolved_entries:
        print(
            "[warn] no PDFs resolved on this host — writing an empty report. "
            "Re-run with --hydrate to fetch, or set "
            "AKSHARAMD_SCIENCE_CORPUS_CACHE to a directory that already "
            "contains the assets.",
            file=sys.stderr,
        )

    captures = _capture_entries(resolved_entries)
    analysis = analyze(captures)

    args.out.mkdir(parents=True, exist_ok=True)
    write_capture_json(captures=captures, out_path=args.out / "capture.json")
    write_analysis_json(analysis=analysis, out_path=args.out / "analysis.json")
    write_markdown_report(
        analysis=analysis,
        captures=captures,
        corpus_name=metadata.corpus_name,
        out_path=args.out / "REPORT.md",
    )

    print(f"[info] wrote report to {args.out}", file=sys.stderr)
    return 0


def _log_hydration(hydration: HydrationResult) -> None:
    if hydration.dry_run:
        print(
            f"[info] hydration dry-run: cache_root={hydration.cache_root}",
            file=sys.stderr,
        )
    for outcome in hydration.outcomes:
        tag = f"[hydration:{outcome.status}]"
        detail = f"{outcome.document_id}"
        if outcome.error:
            detail += f" — {outcome.error}"
        elif outcome.observed_sha256:
            detail += (
                f" — sha256={outcome.observed_sha256[:12]} "
                f"size={outcome.observed_size_bytes}"
            )
        print(f"{tag} {detail}", file=sys.stderr)


def _capture_entries(
    entries: list[ScienceCorpusEntry],
) -> list[LayoutComplexityCapture]:
    captures: list[LayoutComplexityCapture] = []
    for entry in entries:
        try:
            captures.append(
                capture_pdf(document_id=entry.document_id, pdf_path=entry.pdf_path)
            )
            print(
                f"[capture] {entry.document_id}: "
                f"score={captures[-1].decision.score:.1f} "
                f"band={captures[-1].decision.band} "
                f"ocr_frac={captures[-1].ocr_required_fraction:.3f}",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 — informational per-doc
            print(
                f"[capture:error] {entry.document_id}: {exc}",
                file=sys.stderr,
            )
    return captures


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
