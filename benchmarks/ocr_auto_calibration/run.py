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

from typing import Any

from .corpus import (
    CorpusEntry,
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


def _make_local_entry(path_str: str) -> CorpusEntry:
    p = Path(path_str)
    return CorpusEntry(
        document_id=p.stem,
        path=p,
        sha256="",
        profile_class="local",
        expected_backend_by_policy=None,
        source="local",
        notes=f"Explicit --local-doc: {p}",
    )


def _apply_selection(
    entries: list[CorpusEntry],
    doc_ids: list[str],
    local_docs: list[str],
) -> tuple[list[CorpusEntry], dict[str, Any]]:
    """Apply --doc-id filter and --local-doc appends deterministically.

    ``--doc-id`` restricts *entries* to exactly the named document_ids in
    CLI order; unknown IDs raise :class:`SystemExit`. ``--local-doc`` is
    additive: each path becomes a ``source="local"`` :class:`CorpusEntry`
    keyed by filename stem. Missing paths still enter — the harness marks
    them ``skipped_missing_local_asset`` so they surface in the report
    instead of being silently dropped.
    """
    if doc_ids:
        by_id: dict[str, CorpusEntry] = {}
        for e in entries:
            by_id.setdefault(e.document_id, e)
        unknown = [d for d in doc_ids if d not in by_id]
        if unknown:
            raise SystemExit(
                "--doc-id(s) not found in --corpus set: " + ", ".join(unknown)
            )
        selected = [by_id[d] for d in doc_ids]
    else:
        selected = list(entries)

    local_entries = [_make_local_entry(lp) for lp in local_docs]
    missing_local = [
        le.document_id for le in local_entries
        if le.path is None or not le.path.exists()
    ]

    combined = selected + local_entries
    metadata: dict[str, Any] = {
        "requested_doc_ids": list(doc_ids),
        "requested_local_docs": list(local_docs),
        "missing_local_doc_ids": missing_local,
        "final_document_ids": [e.document_id for e in combined],
    }
    return combined, metadata


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
    ap.add_argument(
        "--doc-id",
        action="append",
        default=[],
        dest="doc_id",
        help=(
            "Restrict the run to this document_id. Repeatable. Selection "
            "order matches the CLI order. Unknown IDs raise."
        ),
    )
    ap.add_argument(
        "--local-doc",
        action="append",
        default=[],
        dest="local_doc",
        help=(
            "Add an explicit local PDF path to the run. Repeatable. "
            "Missing files still enter as skipped_missing_local_asset "
            "markers so the report records the intent."
        ),
    )
    args = ap.parse_args(argv)

    treatments = _resolve_treatments(args.treatments)
    entries = _select_corpus(args.corpus)
    entries, selection_metadata = _apply_selection(
        entries, args.doc_id, args.local_doc
    )
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
    report.corpus_provenance["selection"] = selection_metadata

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
