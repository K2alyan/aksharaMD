"""Stage 1 admission batch (B1a-9).

Runs 3 items × 4 parsers = 12 invocations against:
  - 2 olmOCR-Bench PDFs  (on disk; immediately available)
  - 1 DocLayNet page     (from prove-one; NOT in the 280-page selection)

Purpose: verify that Stage1Runner can consume frozen IDs and produce
structurally valid Stage1ExecutionRecords before full Track A execution.
Quality outcomes are NOT inspected here.

Pass criterion: summary.structurally_valid == True
  → n_harness_defect == 0 and n_invocations == n_items × n_parsers

Usage:
    python -m benchmarks.eval_v1.stage1.admission
    python -m benchmarks.eval_v1.stage1.admission --no-firewall
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent.parent

# ---------------------------------------------------------------------------
# Manifest constants.
# ---------------------------------------------------------------------------

MANIFEST_PATH = ROOT / "docs" / "evaluation" / "STAGE1_EXECUTION_MANIFEST.json"

# Raw-byte SHA-256 of STAGE1_EXECUTION_MANIFEST.json as written by
# execution_manifest.run().  Admission refuses to proceed if this drifts.
EXPECTED_MANIFEST_SHA = (
    "1b4609ef7f6fda8b8bd150b7926542145bd04f5a3bc7a466072646c86556d462"
)

# ---------------------------------------------------------------------------
# Admission corpus items.
# ---------------------------------------------------------------------------

_OLMOCR_BASE = ROOT / "tmp" / "olmocr-full-data" / "bench_data" / "pdfs"
_DOCLAYNET_BASE = ROOT / "corpus" / "eval_v1" / "doclaynet"

# Two olmOCR PDFs from distinct categories, one DocLayNet prove-one page.
# The DocLayNet page is NOT in the 280-page frozen selection; it is used
# solely to exercise the doclaynet_val corpus code path in the runner.
_ADMISSION_SPECS: list[dict[str, Any]] = [
    {
        "canonical_id": "arxiv_math/2502.15977_pg21",
        "corpus": "olmocr_bench",
        "pdf_path": _OLMOCR_BASE / "arxiv_math" / "2502.15977_pg21.pdf",
    },
    {
        "canonical_id": "headers_footers/0058e04004009cc0df75aab998d3e107dc646b46_page_1_processed",
        "corpus": "olmocr_bench",
        "pdf_path": (
            _OLMOCR_BASE / "headers_footers"
            / "0058e04004009cc0df75aab998d3e107dc646b46_page_1_processed.pdf"
        ),
    },
    {
        # prove-one page; not in the 280-page frozen selection
        "canonical_id": "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77",
        "corpus": "doclaynet_val",
        "pdf_path": (
            _DOCLAYNET_BASE
            / "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77"
            / "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77.pdf"
        ),
    },
]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_manifest_sha() -> dict:
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"ABORT: manifest not found: {MANIFEST_PATH}")
    actual = _raw_sha256(MANIFEST_PATH)
    if actual != EXPECTED_MANIFEST_SHA:
        raise SystemExit(
            f"ABORT: manifest SHA mismatch.\n"
            f"  expected : {EXPECTED_MANIFEST_SHA}\n"
            f"  actual   : {actual}\n"
            "Regenerate STAGE1_EXECUTION_MANIFEST.json via "
            "execution_manifest.run() and update EXPECTED_MANIFEST_SHA."
        )
    return json.loads(MANIFEST_PATH.read_bytes())


def _check_pdf_paths() -> None:
    missing = [
        spec["pdf_path"]
        for spec in _ADMISSION_SPECS
        if not spec["pdf_path"].exists()
    ]
    if missing:
        lines = "\n".join(f"  {p}" for p in missing)
        raise SystemExit(f"ABORT: admission PDFs not on disk:\n{lines}")


def _try_firewall() -> tuple[bool, tuple[Any, str] | None]:
    """Attempt to create the Windows egress-block firewall rule.

    Returns (blocked, backend_or_None).  Admission continues even if
    firewall setup fails — the structural-validity check does not depend
    on network isolation.  The network_egress_blocked field in every
    record will reflect the actual state.
    """
    try:
        from benchmarks.eval_v1.smoke_b1a_7b.real_firewall import (
            RealFirewallBackend,
            RealPowerShellInvoker,
        )
    except ImportError:
        print("  [firewall] real_firewall import failed; skipping.")
        return False, None

    try:
        backend = RealFirewallBackend(
            invoker=RealPowerShellInvoker(),
        )
        display_name = "AksharaMD-Stage1-Admission-Egress-Block"
        program_path = sys.executable
        backend.create_outbound_block_rule(
            display_name=display_name, program_path=program_path
        )
        print(f"  [firewall] egress-block rule created (program: {program_path})")
        return True, (backend, display_name)
    except Exception as exc:  # noqa: BLE001
        print(
            f"  [firewall] WARNING: could not create egress-block rule: {exc}\n"
            "  Continuing admission without network isolation.\n"
            "  Full Stage 1 execution requires a working firewall."
        )
        return False, None


def _teardown_firewall(state: tuple[Any, str] | None) -> None:
    if state is None:
        return
    backend, display_name = state
    try:
        backend.remove_rule(display_name)
        print(f"  [firewall] egress-block rule removed ({display_name})")
    except Exception as exc:  # noqa: BLE001
        print(f"  [firewall] WARNING: could not remove rule {display_name!r}: {exc}")


def _update_manifest_status(status: str) -> None:
    """Rewrite STAGE1_EXECUTION_MANIFEST.json with updated admission_batch_status."""
    d = json.loads(MANIFEST_PATH.read_bytes())
    d["admission_batch_status"] = status
    MANIFEST_PATH.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
    new_sha = _raw_sha256(MANIFEST_PATH)
    print(f"  manifest updated: admission_batch_status={status!r}")
    print(f"  new manifest SHA : {new_sha}")
    print()
    print("  NOTE: EXPECTED_MANIFEST_SHA in admission.py must be updated to")
    print(f"  {new_sha}")
    print("  if you need to re-run admission.  The runner.py manifest_sha arg")
    print("  should use the SHA of the manifest AS WRITTEN (before this update).")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def run(*, skip_firewall: bool = False) -> bool:
    """Execute the admission batch.  Returns True on pass."""
    print("=== STAGE 1 ADMISSION BATCH ===")
    print(f"  started_utc : {datetime.now(UTC).isoformat(timespec='seconds')}")
    print()

    # 1. Verify manifest integrity.
    print("[1/5] Verifying STAGE1_EXECUTION_MANIFEST.json SHA ...")
    manifest = _verify_manifest_sha()
    manifest_sha = EXPECTED_MANIFEST_SHA
    print(f"  OK — SHA matches {manifest_sha[:16]}…")
    print()

    # 2. Verify all admission PDFs are on disk.
    print("[2/5] Checking admission PDF paths ...")
    _check_pdf_paths()
    for spec in _ADMISSION_SPECS:
        p = spec["pdf_path"]
        print(f"  OK  {spec['corpus']}/{spec['canonical_id'][:40]}  ({p.stat().st_size:,} bytes)")
    print()

    # 3. Firewall setup.
    print("[3/5] Firewall setup ...")
    if skip_firewall:
        print("  [--no-firewall] skipping firewall setup.")
        egress_blocked = False
        fw_state = None
    else:
        egress_blocked, fw_state = _try_firewall()
    print()

    # 4. Build corpus items and run.
    print("[4/5] Running Stage1Runner (3 items × 4 parsers = 12 invocations) ...")
    from .runner import CorpusItem, Stage1Runner

    items = [
        CorpusItem(
            canonical_id=spec["canonical_id"],
            corpus=spec["corpus"],
            pdf_bytes_source=(lambda p: lambda: p.read_bytes())(
                Path(spec["pdf_path"])
            ),
        )
        for spec in _ADMISSION_SPECS
    ]

    run_date = datetime.now(UTC).strftime("%Y-%m-%d")
    run_dir = ROOT / "benchmarks" / "results" / f"stage1-admission-{run_date}"

    model_artifact_shas: dict[str, str | None] = {
        "marker": manifest.get("model_artifact_shas", {}).get("marker"),
        "docling": manifest.get("model_artifact_shas", {}).get("docling"),
    }
    model_cache_paths: dict[str, str] = {
        "marker": manifest.get("model_cache_paths", {}).get("marker", ""),
        "docling": manifest.get("model_cache_paths", {}).get("docling_models", ""),
    }

    try:
        runner = Stage1Runner(
            items=items,
            run_dir=run_dir,
            stage1_manifest_sha256=manifest_sha,
            model_artifact_shas=model_artifact_shas,
            model_cache_paths=model_cache_paths,
            network_egress_blocked=egress_blocked,
            verbose=True,
        )
        summary = runner.run()
    finally:
        _teardown_firewall(fw_state)

    # 5. Evaluate and report.
    print()
    print("[5/5] Admission batch results:")
    print(f"  n_items          : {summary.n_items}")
    print(f"  n_parsers        : {summary.n_parsers}")
    print(f"  n_invocations    : {summary.n_invocations}")
    print(f"  n_executed       : {summary.n_executed}")
    print(f"  n_defect         : {summary.n_defect}")
    print(f"  n_harness_defect : {summary.n_harness_defect}")
    print(f"  wall_clock_secs  : {summary.wall_clock_seconds:.1f}")
    print(f"  structurally_valid : {summary.structurally_valid}")
    print()

    for p in summary.pairs:
        status_str = p.exit_status
        detail = ""
        if p.harness_detail:
            detail = f" [{p.harness_detail[:80]}]"
        elif p.defect_reason:
            detail = f" [{p.defect_reason[:80]}]"
        print(
            f"  {status_str:16s}  {p.parser_id:24s}  "
            f"{p.canonical_id[:40]}…  {p.wall_clock_seconds:.1f}s{detail}"
        )

    passed = summary.structurally_valid
    status = "PASSED" if passed else "FAILED"
    print()
    print(f"=== ADMISSION BATCH {status} ===")
    print()
    _update_manifest_status(status)

    return passed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--no-firewall",
        action="store_true",
        help="Skip Windows firewall egress-block setup (for testing only).",
    )
    args = p.parse_args(argv)
    passed = run(skip_firewall=args.no_firewall)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
