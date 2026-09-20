"""Stage 1 Track A — DocLayNet-Val combined execution + Stage 2 layout scoring.

Runs 280 selected DocLayNet validation pages × 4 parsers = 1120 invocations,
then scores each markdown against the DocLayNet layout ground truth for
Stage 2 table-detection metrics.

Unlike run_track_a_olmocr, this runner does NOT route through Stage1Runner
because Stage 2 scoring requires the raw markdown on disk, which Stage1Runner
does not store.  Instead it calls adapter.compile() directly using the same
subprocess adapter infrastructure, writes both execution_record.json and
parser_output.md, then immediately runs Stage 2 scoring on the stored output.

Network isolation is enforced for Stage 1 (parser execution) only: a Windows
Firewall egress-block rule is created before the first parser invocation and
removed in a finally block.  Stage 2 readiness scoring is a local call and
requires no network.

Resume support: a (page, parser) pair is skipped if BOTH execution_record.json
AND parser_output.md already exist and the record passes identity validation
(canonical_id, parser_id, manifest SHA, terminal exit_status).

Stage 2 resume: stage2_doclaynet_result.json is skipped if it already exists
alongside the other Stage 1 outputs.

Usage:
    python -m benchmarks.eval_v1.stage1.run_track_a_doclaynet
    python -m benchmarks.eval_v1.stage1.run_track_a_doclaynet --no-resume
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent.parent

MANIFEST_PATH = ROOT / "docs" / "evaluation" / "STAGE1_EXECUTION_MANIFEST.json"
SELECTION_PATH = (
    ROOT / "docs" / "evaluation" / "STAGE1_DOCLAYNET_VAL_SELECTION.json"
)

DOCLAYNET_CORPUS_DIR = ROOT / "corpus" / "eval_v1" / "doclaynet"

FIREWALL_RULE_NAME = "AksharaMD-Stage1-TrackA-DocLayNet-Egress-Block"

PARSER_ORDER = ["aksharamd-reference", "marker", "docling", "markitdown"]

# DocLayNet category IDs for regions of interest.
# ID 1 = Caption, ID 9 = Table  (see doclaynet_hf.CATEGORY_ID_TO_NAME)
GT_TABLE_CATEGORY_IDS = {1, 9}  # Caption (1) + Table (9)

STAGE2_SCHEMA_VERSION = "1"


# ---------------------------------------------------------------------------
# Helpers.


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Manifest verification.


def _verify_manifest() -> tuple[dict[str, Any], str]:
    """Verify and load the execution manifest.  Returns (manifest, manifest_sha).

    Reads the manifest SHA from disk at runtime rather than hardcoding it,
    so the runner automatically tracks any authorised re-anchoring.
    """
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"ABORT: manifest not found: {MANIFEST_PATH}")
    manifest_sha = _raw_sha256(MANIFEST_PATH)
    d: dict[str, Any] = json.loads(MANIFEST_PATH.read_bytes())
    if d.get("admission_batch_status") != "PASSED":
        raise SystemExit(
            f"ABORT: admission_batch_status={d.get('admission_batch_status')!r}; "
            "must be PASSED before full execution."
        )
    return d, manifest_sha


# ---------------------------------------------------------------------------
# Selection loading.


def _load_selection() -> list[dict[str, Any]]:
    if not SELECTION_PATH.exists():
        raise SystemExit(f"ABORT: selection file not found: {SELECTION_PATH}")
    sel: dict[str, Any] = json.loads(SELECTION_PATH.read_bytes())
    entries: list[dict[str, Any]] = sel["selected_entries"]
    return entries


# ---------------------------------------------------------------------------
# DocLayNet asset resolution.


def _build_assets(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a {page_hash: DocLayNetAsset} map from cached corpus files."""
    from benchmarks.eval_v1.adapters.doclaynet_v1 import DocLayNetAsset

    assets: dict[str, Any] = {}
    missing: list[str] = []
    for entry in entries:
        page_hash: str = entry["page_hash"]
        page_dir = DOCLAYNET_CORPUS_DIR / page_hash
        manifest_path = page_dir / "manifest.json"
        annotations_path = page_dir / f"{page_hash}.annotations.json"
        png_path = page_dir / f"{page_hash}.png"
        pdf_path = page_dir / f"{page_hash}.pdf"

        if not manifest_path.exists() or not annotations_path.exists():
            missing.append(page_hash)
            continue

        assets[page_hash] = DocLayNetAsset(
            page_hash=page_hash,
            manifest_path=manifest_path,
            annotations_path=annotations_path,
            png_path=png_path,
            pdf_path=pdf_path if pdf_path.exists() else None,
        )
    if missing:
        raise SystemExit(
            f"ABORT: {len(missing)} DocLayNet page(s) not cached under "
            f"{DOCLAYNET_CORPUS_DIR}.\n"
            f"  Missing page_hash examples: {missing[:5]}\n"
            f"  Run the acquisition script first:\n"
            f"    python -m benchmarks.eval_v1.acquisition.doclaynet_hf --mode=full\n"
            f"  or re-run with the selection JSON to download only the 280 selected pages."
        )
    return assets


# ---------------------------------------------------------------------------
# Firewall helpers.


def _setup_firewall() -> tuple[Any, str]:
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
        backend.remove_rule(display_name=display_name)
        print(f"  [firewall] rule removed: {display_name}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [firewall] WARNING: rule removal failed: {exc}")
        print(
            f"  Manual cleanup: Remove-NetFirewallRule -DisplayName '{display_name}'"
        )


# ---------------------------------------------------------------------------
# Environment snapshot helpers.


def _git_commit() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5
            )
            .decode("ascii")
            .strip()
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"


def _cuda_state() -> tuple[str | None, str | None]:
    try:
        import torch
    except ImportError:
        return None, None
    if not torch.cuda.is_available():
        return None, None
    return torch.version.cuda, torch.cuda.get_device_name(0)


def _cpu_cores() -> int:
    try:
        import psutil

        return psutil.cpu_count(logical=False) or 1
    except ImportError:
        return os.cpu_count() or 1


# ---------------------------------------------------------------------------
# Stage 1: build adapters (mirrors runner.build_adapters).


_PENDING_SHA = "0" * 64

WORKER_ARGV_PREFIX = [
    "python",
    "-m",
    "benchmarks.eval_v1.smoke_b1a_7b.workers.main",
]
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "DOCLING_ARTIFACTS_OFFLINE": "1",
}


def _build_adapters(model_artifact_shas: dict[str, str | None]) -> dict[str, Any]:
    from benchmarks.eval_v1.smoke_b1a_7b.real_adapters import (
        RealAdapterConfig,
        SubprocessParserAdapter,
    )
    from benchmarks.eval_v1.smoke_b1a_7b.subprocess_runner import RealSubprocessInvoker

    invoker = RealSubprocessInvoker()
    configs = [
        RealAdapterConfig(
            parser_id="aksharamd-reference",
            package_version="0.3.6",
            package_source_sha256=_PENDING_SHA,
            adapter_source_sha256=_PENDING_SHA,
            parser_model_version=None,
            parser_model_artifact_sha256=None,
            timeout_seconds=120.0,
            is_vlm=False,
        ),
        RealAdapterConfig(
            parser_id="marker",
            package_version="1.10.2",
            package_source_sha256=_PENDING_SHA,
            adapter_source_sha256=_PENDING_SHA,
            parser_model_version="runtime",
            parser_model_artifact_sha256=model_artifact_shas["marker"],
            timeout_seconds=600.0,
            is_vlm=True,
        ),
        RealAdapterConfig(
            parser_id="docling",
            package_version="2.107.0",
            package_source_sha256=_PENDING_SHA,
            adapter_source_sha256=_PENDING_SHA,
            parser_model_version="runtime",
            parser_model_artifact_sha256=model_artifact_shas["docling"],
            timeout_seconds=600.0,
            is_vlm=True,
        ),
        RealAdapterConfig(
            parser_id="markitdown",
            package_version="0.1.6",
            package_source_sha256=_PENDING_SHA,
            adapter_source_sha256=_PENDING_SHA,
            parser_model_version=None,
            parser_model_artifact_sha256=None,
            timeout_seconds=120.0,
            is_vlm=False,
        ),
    ]
    return {
        cfg.parser_id: SubprocessParserAdapter(
            config=cfg,
            subprocess_invoker=invoker,
            worker_argv_prefix=WORKER_ARGV_PREFIX,
            offline_env=OFFLINE_ENV,
        )
        for cfg in configs
    }


# ---------------------------------------------------------------------------
# Stage 1: execution record helpers.


NORMALIZATION_VERSION = "2"
PARSER_EXECUTION_CONTRACT_VERSION = "v1"
CONTRACT_CONFIG_SHA256 = (
    "a0ca496e562cee200c393c146c7ed16efee9b01ee2f94ebdd4e623c936f9baa4"
)


def _build_record(
    *,
    canonical_id: str,
    corpus: str,
    parser_id: str,
    adapter: Any,
    outcome: Any,
    started_at: str,
    finished_at: str,
    wall_clock_seconds: float,
    cpu_user: float,
    cpu_system: float,
    peak_rss: int,
    peak_vram: int | None,
    cuda_events: int | None,
    network_egress_blocked: bool,
    git_commit: str,
    python_version: str,
    platform_string: str,
    cpu_cores: int,
    cuda_version: str | None,
    cuda_device: str | None,
    model_cache_path: str | None,
    manifest_sha: str,
) -> Any:
    from benchmarks.eval_v1.smoke_b1a_7b.adapter_protocol import ParseStatus
    from benchmarks.eval_v1.stage1.record import Stage1ExecutionRecord, compute_pair_id

    is_executed = outcome.status is ParseStatus.EXECUTED
    output_bytes = (
        outcome.markdown.encode("utf-8")
        if (is_executed and outcome.markdown)
        else b""
    )
    stdout_bytes = (outcome.stdout or "").encode("utf-8")
    stderr_bytes = (outcome.stderr or "").encode("utf-8")
    is_cpu_only = parser_id in {"aksharamd-reference", "markitdown"}

    return Stage1ExecutionRecord(
        pair_id=compute_pair_id(canonical_id=canonical_id, parser_id=parser_id),
        canonical_id=canonical_id,
        corpus=corpus,
        parser_id=parser_id,
        parser_package_version=adapter.package_version(),
        parser_package_source_sha256=adapter.package_source_sha256(),
        parser_model_version=adapter.parser_model_version(),
        parser_model_artifact_sha256=adapter.parser_model_artifact_sha256(),
        adapter_source_sha256=adapter.adapter_source_sha256(),
        python_version=python_version,
        platform_string=platform_string,
        git_commit=git_commit,
        cpu_physical_cores=cpu_cores,
        cuda_version=None if is_cpu_only else cuda_version,
        cuda_driver_version=None if is_cpu_only else cuda_version,
        cuda_device_name=None if is_cpu_only else cuda_device,
        model_cache_path=None if is_cpu_only else model_cache_path,
        network_egress_blocked=network_egress_blocked,
        pair_started_at=started_at,
        pair_finished_at=finished_at,
        wall_clock_seconds=wall_clock_seconds,
        cpu_seconds_user=cpu_user,
        cpu_seconds_system=cpu_system,
        peak_rss_bytes=peak_rss,
        peak_vram_bytes=None if is_cpu_only else peak_vram,
        cuda_events=None if is_cpu_only else cuda_events,
        output_bytes=len(output_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        stdout_bytes=len(stdout_bytes),
        stdout_sha256=_sha256_bytes(stdout_bytes),
        stderr_bytes=len(stderr_bytes),
        stderr_sha256=_sha256_bytes(stderr_bytes),
        exit_status="EXECUTED" if is_executed else "DEFECT",
        defect_reason=None if is_executed else outcome.defect_reason,
        normalization_version=NORMALIZATION_VERSION,
        parser_execution_contract_version=PARSER_EXECUTION_CONTRACT_VERSION,
        parser_execution_contract_config_sha256=CONTRACT_CONFIG_SHA256,
        stage1_execution_manifest_sha256=manifest_sha,
    )


def _validate_resume_record(
    path: Path,
    *,
    canonical_id: str,
    parser_id: str,
    manifest_sha: str,
) -> tuple[bool, str]:
    """Return (skip, reason).  skip=True means the record is valid to reuse."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"parse error: {exc}"
    if d.get("canonical_id") != canonical_id:
        return False, (
            f"canonical_id mismatch: "
            f"record={d.get('canonical_id')!r} != expected={canonical_id!r}"
        )
    if d.get("parser_id") != parser_id:
        return False, (
            f"parser_id mismatch: "
            f"record={d.get('parser_id')!r} != expected={parser_id!r}"
        )
    if d.get("stage1_execution_manifest_sha256") != manifest_sha:
        return False, (
            f"manifest SHA mismatch: "
            f"record={str(d.get('stage1_execution_manifest_sha256'))[:16]}… "
            f"!= pinned={manifest_sha[:16]}…"
        )
    status = d.get("exit_status", "")
    if status not in {"EXECUTED", "DEFECT"}:
        return False, f"non-terminal exit_status={status!r}"
    return True, ""


# ---------------------------------------------------------------------------
# Stage 2: table detection helpers.


def _gt_has_table(gt_data: dict[str, Any]) -> bool:
    """True if any annotation in the GT belongs to Table (9) or Caption (1)."""
    for ann in gt_data.get("annotations", []):
        if ann.get("category_id") in GT_TABLE_CATEGORY_IDS:
            return True
    return False


# Markdown pipe-table row: line contains at least one | with non-whitespace
_MD_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|")
# HTML table open tag
_HTML_TABLE_RE = re.compile(r"<table[\s>]", re.IGNORECASE)


def _parser_extracted_table(markdown: str) -> bool:
    """Detect whether markdown contains a rendered table."""
    for line in markdown.splitlines():
        if _MD_TABLE_ROW_RE.match(line):
            return True
    if _HTML_TABLE_RE.search(markdown):
        return True
    return False


def _run_readiness_scorer(
    markdown: str,
    canonical_id: str,
) -> tuple[float | None, list[str]]:
    """Run aksharamd readiness scorer on parser Markdown output.

    SCORING CONTRACT (V1 declared limitation):
      Scores the parser's Markdown output as a Markdown document, NOT the
      original source PDF. Format baseline is 95 (Markdown), not 87 (PDF,
      frozen in manifest §1). Source/geometry detectors (W_TABLE_MISSING,
      W_DROPPED_CONTENT) that require a source PDF are NOT activated and are
      UNMEASURED in V1. This is an explicit declaration of scope.

    score is normalised to [0, 1].  Returns (None, []) if scorer unavailable.
    """
    import tempfile as _tempfile

    try:
        from aksharamd.compiler import Compiler  # noqa: PLC0415
        from aksharamd.scoring.readiness import compute_readiness_score  # noqa: PLC0415

        with _tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(markdown)
            tmp_path = Path(tmp.name)
        try:
            ctx = Compiler().compile(str(tmp_path))
            score = float(compute_readiness_score(ctx)) / 100.0
            codes = [w.code for w in ctx.validation.warnings]
            return score, codes
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        return None, []


def _write_stage2_result(
    pair_dir: Path,
    *,
    canonical_id: str,
    parser_id: str,
    doc_category: str,
    gt_has_table: bool,
    parser_extracted_table: bool,
    readiness_score: float | None,
    warning_codes: list[str],
    execution_status: str,
    defect_reason: str | None,
) -> Path:
    result = {
        "stage2_schema_version": STAGE2_SCHEMA_VERSION,
        "canonical_id": canonical_id,
        "parser_id": parser_id,
        "doc_category": doc_category,
        "gt_has_table": gt_has_table,
        "parser_extracted_table": parser_extracted_table,
        "readiness_score": readiness_score,
        "warning_codes": warning_codes,
        "execution_status": execution_status,
        "defect_reason": defect_reason,
    }
    out_path = pair_dir / "stage2_doclaynet_result.json"
    pair_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Main run logic.


@dataclass
class PairResult:
    canonical_id: str
    parser_id: str
    exit_status: str        # EXECUTED | DEFECT | HARNESS_DEFECT
    wall_clock_seconds: float
    defect_reason: str | None = None
    harness_detail: str | None = None


def run(*, resume: bool = True) -> bool:
    print("=== TRACK A DocLayNet ===")
    print(f"  resume={resume}")
    print()

    # --- Step 1: verify manifest + selection ---
    print("[1/4] Verifying manifest + selection ...")
    manifest, manifest_sha = _verify_manifest()
    run_dir = ROOT / manifest["run_roots"]["track_a_doclaynet"]
    scoring_policy_version = manifest.get("scoring_policy_version", "1.10")
    model_artifact_shas: dict[str, str | None] = {
        "marker": manifest["model_artifact_shas"]["marker"],
        "docling": manifest["model_artifact_shas"]["docling"],
    }
    model_cache_paths: dict[str, str] = {
        "marker": manifest["model_cache_paths"]["marker"],
        "docling": manifest["model_cache_paths"]["docling_models"],
    }
    entries = _load_selection()
    n_pages = len(entries)
    n_source_docs = len({e["original_filename"] for e in entries})
    print(f"  {n_pages} pages, {n_source_docs} source documents")
    print(f"  manifest SHA : {manifest_sha[:24]}…")
    print(f"  run_dir      : {run_dir}")
    print()

    # --- Step 2: check prerequisite data ---
    print("[2/4] Checking prerequisite data ...")
    if not DOCLAYNET_CORPUS_DIR.exists():
        print(
            f"  DocLayNet adapter: ERROR — corpus dir not found: "
            f"{DOCLAYNET_CORPUS_DIR}\n"
            f"  Run the acquisition script first:\n"
            f"    python -m benchmarks.eval_v1.acquisition.doclaynet_hf --mode=full"
        )
        return False

    try:
        assets = _build_assets(entries)
    except SystemExit as exc:
        print(f"  DocLayNet adapter: ERROR — {exc}")
        return False
    print(f"  DocLayNet adapter: OK ({len(assets)} pages cached)")
    print()

    # Build the adapter instance used for GT ingestion.
    from benchmarks.eval_v1.adapters.doclaynet_v1 import DocLayNetV1Adapter

    dl_adapter = DocLayNetV1Adapter(assets)

    # --- Step 3: run Stage 1 + Stage 2 ---
    print("[3/4] Running (Stage 1 + Stage 2) ...")
    print("[3a]  Establishing network isolation (Windows Firewall) ...")
    firewall_backend, rule_name = _setup_firewall()
    print()

    adapters = _build_adapters(model_artifact_shas)

    # Environment snapshot (captured once).
    git_commit = _git_commit()
    python_version = sys.version.split()[0]
    platform_string = platform.platform()
    cpu_cores = _cpu_cores()
    cuda_version, cuda_device = _cuda_state()

    total = n_pages * len(PARSER_ORDER)
    done = 0
    pairs: list[PairResult] = []

    try:
        for entry in entries:
            page_hash: str = entry["page_hash"]
            doc_category: str = entry["doc_category"]
            canonical_id = f"doclaynet_val/{page_hash}"

            # Load PDF bytes once per page.
            try:
                pdf_path = dl_adapter.acquire(page_hash)
                pdf_bytes = pdf_path.read_bytes()
            except Exception as exc:  # noqa: BLE001
                for pid in PARSER_ORDER:
                    done += 1
                    print(
                        f"  [{done}/{total}] {doc_category}/{page_hash[:16]}…"
                        f" × {pid} -> HARNESS_DEFECT (pdf load: {exc})"
                    )
                    pairs.append(
                        PairResult(
                            canonical_id=canonical_id,
                            parser_id=pid,
                            exit_status="HARNESS_DEFECT",
                            wall_clock_seconds=0.0,
                            harness_detail=f"pdf load failed: {exc}",
                        )
                    )
                continue

            for parser_id in PARSER_ORDER:
                done += 1
                adapter = adapters[parser_id]
                pair_dir = (
                    run_dir
                    / "doclaynet_val"
                    / page_hash
                    / parser_id
                )
                record_path = pair_dir / "execution_record.json"
                md_path = pair_dir / "parser_output.md"
                s2_path = pair_dir / "stage2_doclaynet_result.json"

                # Resume: skip Stage 1 if both record + markdown exist.
                if resume and record_path.exists() and md_path.exists():
                    skip, skip_reason = _validate_resume_record(
                        record_path,
                        canonical_id=canonical_id,
                        parser_id=parser_id,
                        manifest_sha=manifest_sha,
                    )
                    if skip:
                        existing = json.loads(
                            record_path.read_text(encoding="utf-8")
                        )
                        ex_status = existing.get("exit_status", "?")
                        print(
                            f"  [{done}/{total}] SKIP (resume) "
                            f"{doc_category}/{page_hash[:16]}… × {parser_id}"
                            f" [{ex_status}]"
                        )
                        pairs.append(
                            PairResult(
                                canonical_id=canonical_id,
                                parser_id=parser_id,
                                exit_status=ex_status,
                                wall_clock_seconds=existing.get(
                                    "wall_clock_seconds", 0.0
                                ),
                                defect_reason=existing.get("defect_reason"),
                            )
                        )
                        # Stage 2: also resume if result already exists.
                        if not s2_path.exists():
                            _run_stage2_for_pair(
                                pair_dir=pair_dir,
                                md_path=md_path,
                                dl_adapter=dl_adapter,
                                page_hash=page_hash,
                                canonical_id=canonical_id,
                                parser_id=parser_id,
                                doc_category=doc_category,
                                exit_status=ex_status,
                                defect_reason=existing.get("defect_reason"),
                            )
                        continue
                    else:
                        print(
                            f"  [{done}/{total}] RE-RUN (resume invalid: {skip_reason})"
                            f" {doc_category}/{page_hash[:16]}… × {parser_id}"
                        )

                # --- Stage 1 execution ---
                t0 = time.monotonic()
                started_at = _now_utc()

                try:
                    import psutil

                    proc = psutil.Process()
                    cpu_times_before = proc.cpu_times()
                    rss_before = proc.memory_info().rss
                except Exception:  # noqa: BLE001
                    proc = None
                    cpu_times_before = None
                    rss_before = 0

                outcome = adapter.compile(pdf_bytes, canonical_id)

                t1 = time.monotonic()
                finished_at = _now_utc()
                wall_secs = t1 - t0

                cpu_user, cpu_sys, peak_rss = 0.0, 0.0, 0
                if proc is not None and cpu_times_before is not None:
                    try:
                        cpu_times_after = proc.cpu_times()
                        cpu_user = max(
                            0.0, cpu_times_after.user - cpu_times_before.user
                        )
                        cpu_sys = max(
                            0.0, cpu_times_after.system - cpu_times_before.system
                        )
                        peak_rss = max(proc.memory_info().rss - rss_before, 0)
                    except Exception:  # noqa: BLE001
                        pass

                is_vlm = parser_id in {"marker", "docling"}
                peak_vram: int | None = None
                if is_vlm:
                    try:
                        import torch

                        if torch.cuda.is_available():
                            peak_vram = torch.cuda.max_memory_allocated()
                            torch.cuda.reset_peak_memory_stats()
                    except Exception:  # noqa: BLE001
                        pass

                try:
                    from benchmarks.eval_v1.stage1.record import (
                        write_execution_record,
                    )

                    record = _build_record(
                        canonical_id=canonical_id,
                        corpus="doclaynet_val",
                        parser_id=parser_id,
                        adapter=adapter,
                        outcome=outcome,
                        started_at=started_at,
                        finished_at=finished_at,
                        wall_clock_seconds=wall_secs,
                        cpu_user=cpu_user,
                        cpu_system=cpu_sys,
                        peak_rss=peak_rss,
                        peak_vram=peak_vram,
                        cuda_events=0 if is_vlm else None,
                        network_egress_blocked=True,
                        git_commit=git_commit,
                        python_version=python_version,
                        platform_string=platform_string,
                        cpu_cores=cpu_cores,
                        cuda_version=cuda_version,
                        cuda_device=cuda_device,
                        model_cache_path=model_cache_paths.get(parser_id),
                        manifest_sha=manifest_sha,
                    )
                    pair_dir.mkdir(parents=True, exist_ok=True)
                    write_execution_record(record_path, record)

                    exit_status = record.exit_status
                    defect_reason = record.defect_reason

                    # Write markdown alongside record (needed for Stage 2).
                    from benchmarks.eval_v1.smoke_b1a_7b.adapter_protocol import (
                        ParseStatus,
                    )

                    raw_markdown = ""
                    if outcome.status is ParseStatus.EXECUTED and outcome.markdown:
                        raw_markdown = outcome.markdown
                    md_path.write_text(raw_markdown, encoding="utf-8")

                    print(
                        f"  [{done}/{total}] {doc_category}/{page_hash[:16]}…"
                        f" × {parser_id} -> {exit_status}"
                        + (f" ({defect_reason})" if defect_reason else "")
                        + f" ({wall_secs:.1f}s)"
                    )
                    pairs.append(
                        PairResult(
                            canonical_id=canonical_id,
                            parser_id=parser_id,
                            exit_status=exit_status,
                            wall_clock_seconds=wall_secs,
                            defect_reason=defect_reason,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"  [{done}/{total}] {doc_category}/{page_hash[:16]}…"
                        f" × {parser_id} -> HARNESS_DEFECT ({exc})"
                    )
                    pairs.append(
                        PairResult(
                            canonical_id=canonical_id,
                            parser_id=parser_id,
                            exit_status="HARNESS_DEFECT",
                            wall_clock_seconds=wall_secs,
                            harness_detail=str(exc),
                        )
                    )
                    continue

                # --- Stage 2 scoring (inline, after Stage 1 for this pair) ---
                _run_stage2_for_pair(
                    pair_dir=pair_dir,
                    md_path=md_path,
                    dl_adapter=dl_adapter,
                    page_hash=page_hash,
                    canonical_id=canonical_id,
                    parser_id=parser_id,
                    doc_category=doc_category,
                    exit_status=exit_status,
                    defect_reason=defect_reason,
                )
    finally:
        _teardown_firewall(firewall_backend, rule_name)

    # --- Step 4: summary ---
    print()
    print("[4/4] Writing summary ...")
    n_executed = sum(1 for p in pairs if p.exit_status == "EXECUTED")
    n_defect = sum(1 for p in pairs if p.exit_status == "DEFECT")
    n_harness = sum(1 for p in pairs if p.exit_status == "HARNESS_DEFECT")

    summary_path = run_dir.parent / f"{run_dir.name}-summary.json"
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    summary_dict = {
        "corpus": "doclaynet_val",
        "run_dir": str(run_dir),
        "manifest_sha": manifest_sha,
        "n_pages": n_pages,
        "n_parsers": len(PARSER_ORDER),
        "n_executed": n_executed,
        "n_defect": n_defect,
        "n_harness_defect": n_harness,
        "scoring_policy_version": scoring_policy_version,
        "written_utc": _now_utc(),
    }
    summary_path.write_text(json.dumps(summary_dict, indent=2), encoding="utf-8")

    print()
    print("=== SUMMARY ===")
    print(f"  n_pages      : {n_pages}")
    print(f"  n_parsers    : {len(PARSER_ORDER)}")
    print(f"  n_executed   : {n_executed}")
    print(f"  n_defect     : {n_defect}")
    print(f"  Summary written: {summary_path}")

    structurally_valid = n_harness == 0
    return structurally_valid


def _run_stage2_for_pair(
    *,
    pair_dir: Path,
    md_path: Path,
    dl_adapter: Any,
    page_hash: str,
    canonical_id: str,
    parser_id: str,
    doc_category: str,
    exit_status: str,
    defect_reason: str | None,
) -> None:
    """Score one (page, parser) pair for Stage 2 and write the result file."""
    s2_path = pair_dir / "stage2_doclaynet_result.json"
    if s2_path.exists():
        return

    # Read GT.
    try:
        gt = dl_adapter.ingest_ground_truth(page_hash)
    except Exception as exc:  # noqa: BLE001
        _write_stage2_result(
            pair_dir,
            canonical_id=canonical_id,
            parser_id=parser_id,
            doc_category=doc_category,
            gt_has_table=False,
            parser_extracted_table=False,
            readiness_score=None,
            warning_codes=[],
            execution_status="DEFECT",
            defect_reason=f"gt_ingest_failed: {exc}",
        )
        return

    gt_table = _gt_has_table(gt.data)

    # Read stored markdown.
    if exit_status != "EXECUTED" or not md_path.exists():
        _write_stage2_result(
            pair_dir,
            canonical_id=canonical_id,
            parser_id=parser_id,
            doc_category=doc_category,
            gt_has_table=gt_table,
            parser_extracted_table=False,
            readiness_score=None,
            warning_codes=[],
            execution_status="DEFECT",
            defect_reason=defect_reason or "parser_did_not_execute",
        )
        return

    markdown = md_path.read_text(encoding="utf-8")
    extracted = _parser_extracted_table(markdown)
    readiness_score, warning_codes = _run_readiness_scorer(markdown, canonical_id)

    _write_stage2_result(
        pair_dir,
        canonical_id=canonical_id,
        parser_id=parser_id,
        doc_category=doc_category,
        gt_has_table=gt_table,
        parser_extracted_table=extracted,
        readiness_score=readiness_score,
        warning_codes=warning_codes,
        execution_status="EXECUTED",
        defect_reason=None,
    )


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
