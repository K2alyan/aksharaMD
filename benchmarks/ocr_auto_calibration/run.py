"""CLI entry point for the OCR Auto Policy v1 calibration harness.

Usage::

    python -m benchmarks.ocr_auto_calibration.run \
        [--dry-run] \
        [--corpus parsebench|synthetic|failure|all] \
        [--treatments tesseract,unlimited_ocr,auto] \
        [--output-dir <path>] \
        [--resume]

``--resume`` honours the cache. Without it, cached results are ignored and
every treatment is re-run. The CLI never opens the review pipeline; it
only writes artifacts under ``--output-dir`` (default
``benchmarks/ocr_auto_calibration/results/<ISO date>/``).
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - benign; used only for `git rev-parse`
import sys
from datetime import date
from pathlib import Path

from .corpus import (
    enumerate_corpus,
    list_failure_fixtures,
    list_synthetic_fixtures,
    load_parsebench_corpus,
)
from .harness import run_harness
from .report import write_markdown
from .review_queue import build_review_queue, write_review_queue
from .schema import HARNESS_SCHEMA_VERSION, Treatment


def _resolve_treatments(spec: str) -> tuple[Treatment, ...]:
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    resolved: list[Treatment] = []
    for p in parts:
        if p not in ("tesseract", "unlimited_ocr", "auto"):
            raise SystemExit(f"unknown treatment: {p}")
        resolved.append(p)  # type: ignore[arg-type]
    return tuple(resolved)


def _current_commit() -> str:
    try:
        proc = subprocess.run(  # nosec B603 B607 - fixed argv
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _current_model_revision() -> str:
    try:
        from aksharamd.plugins.ocr_backends.unlimited_ocr.adapter import (
            _UNLIMITED_OCR_MODEL_REVISION,
        )
        return str(_UNLIMITED_OCR_MODEL_REVISION or "unpinned")
    except ImportError:
        return "unknown"


def _select_corpus(scope: str) -> list:
    if scope == "parsebench":
        return load_parsebench_corpus()
    if scope == "synthetic":
        return list_synthetic_fixtures()
    if scope == "failure":
        return list_failure_fixtures()
    if scope == "all":
        return enumerate_corpus()
    raise SystemExit(f"unknown corpus scope: {scope}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--corpus",
        choices=("parsebench", "synthetic", "failure", "all"),
        default="all",
    )
    ap.add_argument("--treatments", default="tesseract,unlimited_ocr,auto")
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)

    treatments = _resolve_treatments(args.treatments)
    entries = _select_corpus(args.corpus)
    commit = _current_commit()
    model_revision = _current_model_revision()

    output_dir = args.output_dir or (
        Path(__file__).resolve().parent
        / "results"
        / f"{date.today().isoformat()}_schema_v{HARNESS_SCHEMA_VERSION}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_harness(
        entries=entries,
        treatments=treatments,
        dry_run=args.dry_run,
        use_cache=args.resume,
        aksharamd_commit=commit,
        model_revision=model_revision,
    )

    # JSON report
    (output_dir / "run_report.json").write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    # Markdown report
    write_markdown(
        report, output_dir / f"OCR_AUTO_CALIBRATION_{date.today().isoformat()}.md"
    )
    # Review queue
    queue = build_review_queue(report.documents)
    write_review_queue(queue, output_dir / "review_queue.json")

    # Deliberate CLI output.
    print(f"Wrote artifacts to {output_dir}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
