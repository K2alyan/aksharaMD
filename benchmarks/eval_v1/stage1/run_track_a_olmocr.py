"""Stage 1 Track A — olmOCR-Bench full execution (B1a-9).

Runs all 1,403 olmOCR-Bench PDFs × 4 parsers = 5,612 invocations.

Network isolation is enforced: Windows Firewall egress-block rule is
created before the first invocation and removed in a finally block.
The run aborts if the firewall cannot be established.

Resume support: invocations with a fully validated execution_record.json
(matching canonical_id, parser_id, and manifest SHA) are skipped.

Usage:
    python -m benchmarks.eval_v1.stage1.run_track_a_olmocr
    python -m benchmarks.eval_v1.stage1.run_track_a_olmocr --no-resume
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent.parent

MANIFEST_PATH = ROOT / "docs" / "evaluation" / "STAGE1_EXECUTION_MANIFEST.json"

# Raw-byte SHA-256 of STAGE1_EXECUTION_MANIFEST.json after admission PASSED
# and VLM model artifact SHAs added (checks 1–11 all green).
# This is the provenance anchor embedded in every Stage 1 execution record.
MANIFEST_SHA = (
    "4b5116f4a965f2b4e653705827d3f0a4115b2b4b4e3a1b2af4f7e234bd26b3db"
)

OLMOCR_PDFS_DIR = ROOT / "tmp" / "olmocr-full-data" / "bench_data" / "pdfs"

FIREWALL_RULE_NAME = "AksharaMD-Stage1-TrackA-olmOCR-Egress-Block"


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"ABORT: manifest not found: {MANIFEST_PATH}")
    actual = _raw_sha256(MANIFEST_PATH)
    if actual != MANIFEST_SHA:
        raise SystemExit(
            f"ABORT: manifest SHA mismatch.\n"
            f"  expected : {MANIFEST_SHA}\n"
            f"  actual   : {actual}"
        )
    d = json.loads(MANIFEST_PATH.read_bytes())
    if d.get("admission_batch_status") != "PASSED":
        raise SystemExit(
            f"ABORT: admission_batch_status={d.get('admission_batch_status')!r}; "
            "must be PASSED before full execution."
        )
    return d


def _build_items():
    from .runner import CorpusItem

    if not OLMOCR_PDFS_DIR.exists():
        raise SystemExit(f"ABORT: olmOCR PDFs dir not found: {OLMOCR_PDFS_DIR}")

    items = []
    for pdf_path in sorted(OLMOCR_PDFS_DIR.rglob("*.pdf")):
        canonical_id = (
            pdf_path.relative_to(OLMOCR_PDFS_DIR).with_suffix("").as_posix()
        )
        items.append(
            CorpusItem(
                canonical_id=canonical_id,
                corpus="olmocr_bench",
                pdf_bytes_source=(lambda p: lambda: p.read_bytes())(pdf_path),
            )
        )
    return items


def _setup_firewall() -> tuple[Any, str]:
    """Create egress-block rule.  Raises on any failure — fail-closed."""
    from benchmarks.eval_v1.smoke_b1a_7b.real_firewall import (
        RealFirewallBackend,
        RealPowerShellInvoker,
    )
    backend = RealFirewallBackend(invoker=RealPowerShellInvoker())
    backend.create_outbound_block_rule(
        display_name=FIREWALL_RULE_NAME, program_path=sys.executable
    )
    print(f"  [firewall] egress-block rule created: {FIREWALL_RULE_NAME}")
    return backend, FIREWALL_RULE_NAME


def _teardown_firewall(backend: Any, display_name: str) -> None:
    try:
        backend.remove_rule(display_name)
        print(f"  [firewall] rule removed: {display_name}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [firewall] WARNING: rule removal failed: {exc}")
        print(f"  Manual cleanup: Remove-NetFirewallRule -DisplayName '{display_name}'")


def run(*, resume: bool = True) -> bool:
    print("=== STAGE 1 TRACK A — olmOCR-Bench ===")
    print(f"  resume={resume}")
    print()

    print("[1/4] Verifying manifest ...")
    manifest = _verify_manifest()
    run_dir = ROOT / manifest["run_roots"]["track_a_olmocr"]
    model_artifact_shas: dict[str, str | None] = {
        "marker": manifest["model_artifact_shas"]["marker"],
        "docling": manifest["model_artifact_shas"]["docling"],
    }
    model_cache_paths: dict[str, str] = {
        "marker": manifest["model_cache_paths"]["marker"],
        "docling": manifest["model_cache_paths"]["docling_models"],
    }
    print("  OK — admission_batch_status=PASSED")
    print(f"  run_dir : {run_dir}")
    print(f"  marker artifact SHA  : {(model_artifact_shas['marker'] or '')[:24]}…")
    print(f"  docling artifact SHA : {(model_artifact_shas['docling'] or '')[:24]}…")
    print()

    print("[2/4] Building olmOCR corpus items ...")
    items = _build_items()
    print(f"  {len(items)} PDFs × 4 parsers = {len(items) * 4} invocations")
    print()

    print("[3/4] Establishing network isolation (Windows Firewall) ...")
    backend, rule_name = _setup_firewall()
    print()

    print("[4/4] Running Stage1Runner ...")
    from .runner import Stage1Runner

    try:
        runner = Stage1Runner(
            items=items,
            run_dir=run_dir,
            stage1_manifest_sha256=MANIFEST_SHA,
            model_artifact_shas=model_artifact_shas,
            model_cache_paths=model_cache_paths,
            network_egress_blocked=True,
            verbose=True,
            resume=resume,
        )
        summary = runner.run()
    finally:
        _teardown_firewall(backend, rule_name)

    print()
    print("=== TRACK A olmOCR SUMMARY ===")
    print(f"  n_items            : {summary.n_items}")
    print(f"  n_parsers          : {summary.n_parsers}")
    print(f"  n_invocations      : {summary.n_invocations}")
    print(f"  n_executed         : {summary.n_executed}")
    print(f"  n_defect           : {summary.n_defect}")
    print(f"  n_harness_defect   : {summary.n_harness_defect}")
    print(f"  wall_clock_secs    : {summary.wall_clock_seconds:.1f}")
    print(f"  structurally_valid : {summary.structurally_valid}")

    defects = [p for p in summary.pairs if p.exit_status == "DEFECT"]
    harness = [p for p in summary.pairs if p.exit_status == "HARNESS_DEFECT"]
    if defects:
        print(f"\n  Parser defects ({len(defects)}):")
        for p in defects[:20]:
            print(f"    {p.parser_id:24s}  {p.canonical_id[:40]}  {p.defect_reason}")
        if len(defects) > 20:
            print(f"    ... and {len(defects) - 20} more")
    if harness:
        print(f"\n  Harness defects ({len(harness)}):")
        for p in harness[:10]:
            print(
                f"    {p.parser_id:24s}  {p.canonical_id[:40]}  "
                f"{p.harness_detail}"
            )

    summary_path = run_dir.parent / f"{run_dir.name}-summary.json"
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    summary_dict = {
        "run_dir": str(run_dir),
        "manifest_sha": MANIFEST_SHA,
        "started_at": summary.started_at,
        "finished_at": summary.finished_at,
        "wall_clock_seconds": summary.wall_clock_seconds,
        "n_items": summary.n_items,
        "n_parsers": summary.n_parsers,
        "n_invocations": summary.n_invocations,
        "n_executed": summary.n_executed,
        "n_defect": summary.n_defect,
        "n_harness_defect": summary.n_harness_defect,
        "structurally_valid": summary.structurally_valid,
    }
    summary_path.write_text(json.dumps(summary_dict, indent=2), encoding="utf-8")
    print(f"\n  Summary written: {summary_path}")

    return summary.structurally_valid


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-run all invocations even if validated records exist.",
    )
    args = p.parse_args(argv)
    passed = run(resume=not args.no_resume)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
