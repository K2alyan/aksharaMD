"""Stage 1 Track C — Consequential Validity (B1a-9).

Tests Claim 4: ρ(readiness_score, degradation) < 0, where
    degradation = EM(aksharamd-reference) − EM(this_parser).

Corpora:
  - QASPER  : n=50 documents  (adapter: benchmarks/eval_v1/adapters/qasper_v1.py)
  - TAT-DQA : n=30 documents  (adapter: benchmarks/eval_v1/adapters/tat_dqa_v1.py)
  - MMLongBench-Doc : NOT_EXECUTABLE — no adapter exists in V1

Three-phase execution:

  Phase 1  Parser execution (firewall UP — no network egress).
           For each (doc, parser): acquire PDF → compile → write
           execution_record.json + parser_output.md.
           Reuses the SubprocessParserAdapter and record-building
           infrastructure from runner.py.  The markdown from each
           successful invocation is written alongside the record.

  Phase 2  LLM evaluation (firewall DOWN — network required).
           Loads stored parser_output.md, calls claude-sonnet-4-6 at
           temperature=0.0 for each QA pair, computes EM.
           Writes or updates track_c_result.json per (doc, parser).

  Phase 3  Readiness scoring.
           Runs stored markdown through the AksharaMD Compiler
           (MarkdownParser path) to get readiness_score + warning_codes.
           Updates track_c_result.json with the computed values.

Prompt SHA-256 (canonical LF bytes):
    2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601

NOTE: Phase 1 requires elevated privileges (Windows Firewall rule creation).

Usage:
    python -m benchmarks.eval_v1.stage1.run_track_c
    python -m benchmarks.eval_v1.stage1.run_track_c --no-resume
    python -m benchmarks.eval_v1.stage1.run_track_c --phase 1
    python -m benchmarks.eval_v1.stage1.run_track_c --phase 2
    python -m benchmarks.eval_v1.stage1.run_track_c --phase 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import string
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent.parent

MANIFEST_PATH = ROOT / "docs" / "evaluation" / "STAGE1_EXECUTION_MANIFEST.json"
PROMPT_PATH = ROOT / "docs" / "evaluation" / "TRACK_C_PROMPT_V1.txt"

# Raw-byte SHA-256 of STAGE1_EXECUTION_MANIFEST.json after admission PASSED,
# VLM model artifact SHAs, and the Phase 2 evaluation anchor were added.  The
# manifest records 8834dc4 as the evaluation-logic commit; this pin is the
# final link in that provenance chain and intentionally does not require the
# manifest to refer to the commit that updates this constant.
MANIFEST_SHA = (
    "cf8f9f4fbd4bcb9d4df5e816638afbbcf3ab6e955a4f4888508da49552ceb360"
)

# Canonical-LF SHA-256 of docs/evaluation/TRACK_C_PROMPT_V1.txt (preregistered).
PROMPT_SHA256 = (
    "2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601"
)

MODEL_ID = "claude-sonnet-4-6"
TEMPERATURE = 0.0

# Bumped when metric logic, scoring contract, or result schema changes.
# Resume REJECTS records with a different version even if llm_evaluated=True.
METRIC_SCHEMA_VERSION = "3"

# Identifies the scoring contract used in Phase 3.
# "markdown_only_v1" = parser Markdown scored as .md (baseline 95, no source PDF).
# See docs/evaluation/V1_PROTOCOL_DEVIATIONS.md D-002.
SCORING_CONTRACT_ID = "markdown_only_v1"

FIREWALL_RULE_NAME = "AksharaMD-Stage1-TrackC-Egress-Block"

PARSER_ORDER = ["aksharamd-reference", "marker", "docling", "markitdown"]
GOLD_ARM_ID = "corpus_gold"
EVAL_PARSER_ORDER = PARSER_ORDER + [GOLD_ARM_ID]

# n_target from manifest §track_c.corpora
QASPER_N_TARGET = 50
TATDQA_N_TARGET = 30

SCORING_POLICY_VERSION_REQUIRED = "1.10"

# Parser-execution contract constants (mirror runner.py; must not drift).
PARSER_EXECUTION_CONTRACT_VERSION = "v1"
NORMALIZATION_VERSION = "2"
CONTRACT_CONFIG_SHA256 = (
    "a0ca496e562cee200c393c146c7ed16efee9b01ee2f94ebdd4e623c936f9baa4"
)
WORKER_ARGV_PREFIX = [
    "python", "-m", "benchmarks.eval_v1.smoke_b1a_7b.workers.main",
]
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "DOCLING_ARTIFACTS_OFFLINE": "1",
}
_PENDING_SHA = "0" * 64


# ---------------------------------------------------------------------------
# Utilities.


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_phase2_result_compatible(
    existing: dict[str, Any],
    current_input_sha256: str | None,
) -> bool:
    """Return True iff an existing track_c_result.json can be reused on resume.

    Checks:
      - llm_evaluated must be True (unevaluated records are never skipped)
      - no retryable LLM infrastructure errors may remain
      - metric_schema_version, prompt_sha256, scoring_contract_id must all match
      - If existing record carries input_sha256, it must equal current_input_sha256
        (None current hash means the markdown file is absent → reject)
    """
    if not existing.get("llm_evaluated"):
        return False
    if int(existing.get("n_llm_errors") or 0) != 0:
        return False
    if any(
        row.get("status") == "llm_error"
        for row in existing.get("qa_results", [])
    ):
        return False
    if existing.get("metric_schema_version") != METRIC_SCHEMA_VERSION:
        return False
    if existing.get("prompt_sha256") != PROMPT_SHA256:
        return False
    if existing.get("scoring_contract_id") != SCORING_CONTRACT_ID:
        return False
    stored_hash = existing.get("input_sha256")
    # Fail closed: a record without input_sha256 cannot be verified and must be
    # re-evaluated. Also fails when the markdown file is absent (current=None).
    if stored_hash is None or current_input_sha256 != stored_hash:
        return False
    return True


def _restorable_phase2_qa_results(
    existing: dict[str, Any],
    current_input_sha256: str,
) -> dict[int, dict[str, Any]]:
    """Return durable question results that can be reused without another API call.

    Only successful answers and deterministic no-gold skips are reusable.  An
    ``llm_error`` is evidence that no answer was obtained, so it must always be
    retried after the external service recovers.
    """
    if existing.get("input_sha256") != current_input_sha256:
        return {}
    if existing.get("metric_schema_version") != METRIC_SCHEMA_VERSION:
        return {}
    if existing.get("prompt_sha256") != PROMPT_SHA256:
        return {}
    if existing.get("scoring_contract_id") != SCORING_CONTRACT_ID:
        return {}

    reusable: dict[int, dict[str, Any]] = {}
    for row in existing.get("qa_results", []):
        question_id = row.get("question_id")
        if question_id is None or row.get("status") not in {"answered", "no_gold"}:
            continue
        reusable[int(question_id)] = row
    return reusable


def _is_terminal_llm_error(exc: Exception) -> bool:
    """Return True for deterministic provider errors that should abort the run."""
    message = str(exc).lower()
    return (
        "credit balance is too low" in message
        or "insufficient credit" in message
        or "invalid_request_error" in message
    )


def _canonical_lf_sha256(path: Path) -> str:
    raw = path.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


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


def _cuda_state() -> tuple[str | None, str | None, str | None]:
    try:
        import torch
    except ImportError:
        return None, None, None
    if not torch.cuda.is_available():
        return None, None, None
    return torch.version.cuda, torch.version.cuda, torch.cuda.get_device_name(0)


def _cpu_cores() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=False) or 1
    except ImportError:
        return os.cpu_count() or 1


# ---------------------------------------------------------------------------
# Manifest and prompt verification.


def _verify_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"ABORT: manifest not found: {MANIFEST_PATH}")
    actual = _raw_sha256(MANIFEST_PATH)
    if actual != MANIFEST_SHA:
        raise SystemExit(
            f"ABORT: manifest SHA mismatch.\n"
            f"  expected : {MANIFEST_SHA}\n"
            f"  actual   : {actual}"
        )
    d: dict[str, Any] = json.loads(MANIFEST_PATH.read_bytes())
    if d.get("admission_batch_status") != "PASSED":
        raise SystemExit(
            f"ABORT: admission_batch_status={d.get('admission_batch_status')!r}; "
            "must be PASSED before full execution."
        )
    spv = d.get("scoring_policy_version", "")
    if spv != SCORING_POLICY_VERSION_REQUIRED:
        raise SystemExit(
            f"ABORT: scoring_policy_version={spv!r}; "
            f"expected {SCORING_POLICY_VERSION_REQUIRED!r}"
        )
    return d


def _verify_prompt() -> str:
    """Read the prompt, verify its canonical-LF SHA-256, return content string."""
    if not PROMPT_PATH.exists():
        raise SystemExit(f"ABORT: prompt file not found: {PROMPT_PATH}")
    actual = _canonical_lf_sha256(PROMPT_PATH)
    if actual != PROMPT_SHA256:
        raise SystemExit(
            f"ABORT: prompt SHA-256 mismatch.\n"
            f"  expected : {PROMPT_SHA256}\n"
            f"  actual   : {actual}\n"
            f"  file     : {PROMPT_PATH}"
        )
    raw = PROMPT_PATH.read_bytes().replace(b"\r\n", b"\n")
    return raw.decode("utf-8")


# ---------------------------------------------------------------------------
# Corpus building.


def _build_qasper_items() -> list[dict[str, Any]]:
    """Select up to QASPER_N_TARGET QASPER dev documents with cached PDFs.

    PDF filenames in .cache/qasper/ are ``<arxiv_id>-<12hexchars>.pdf``.
    The arxiv_id maps directly to keys in the dev JSON index.
    Selection is stable (sorted by arxiv_id).
    """
    cache_root = ROOT / ".cache" / "qasper"
    if not cache_root.exists():
        return []

    dev_json = cache_root / "qasper-dev-v0.3.json"
    tar_path = cache_root / "qasper-train-dev-v0.3.tgz"
    if not dev_json.exists():
        if not tar_path.exists():
            return []
        import tarfile
        with tarfile.open(tar_path, "r:gz") as tar:
            for m in tar.getmembers():
                if m.name.endswith("qasper-dev-v0.3.json"):
                    fobj = tar.extractfile(m)
                    if fobj is not None:
                        dev_json.parent.mkdir(parents=True, exist_ok=True)
                        dev_json.write_bytes(fobj.read())
                    break
    if not dev_json.exists():
        return []

    dev_index: dict[str, Any] = json.loads(dev_json.read_text(encoding="utf-8"))

    # Filename pattern: <arxiv_id>-<12-hex-chars>.pdf
    _HASH_SUFFIX = re.compile(r"-[0-9a-f]{12}\.pdf$")
    arxiv_to_path: dict[str, Path] = {}
    for pdf_path in sorted(cache_root.glob("*.pdf")):
        name = pdf_path.name
        m = _HASH_SUFFIX.search(name)
        if m is None:
            continue
        arxiv_id = name[: m.start()]
        arxiv_to_path[arxiv_id] = pdf_path

    selected: list[dict[str, Any]] = []
    for arxiv_id in sorted(arxiv_to_path.keys()):
        if arxiv_id not in dev_index:
            continue
        selected.append({"doc_id": arxiv_id, "pdf_path": arxiv_to_path[arxiv_id]})
        if len(selected) >= QASPER_N_TARGET:
            break

    return selected


def _build_tatdqa_items() -> list[dict[str, Any]]:
    """Select up to TATDQA_N_TARGET TAT-DQA dev documents with cached PDFs.

    UIDs come from tatdqa_dataset_dev.json; PDFs are in tat_docs/dev/<uid>.pdf.
    Selection is stable (sorted by uid).
    """
    cache_root = ROOT / ".cache" / "tat_dqa"
    dev_json = cache_root / "tatdqa_dataset_dev.json"
    if not dev_json.exists():
        return []

    data: list[dict[str, Any]] = json.loads(dev_json.read_text(encoding="utf-8"))
    pdf_dir = cache_root / "tat_docs" / "dev"
    if not pdf_dir.exists():
        return []

    seen: set[str] = set()
    all_uids: list[str] = []
    for rec in data:
        doc = rec.get("doc", {}) or {}
        uid = doc.get("uid") or rec.get("doc_uid") or ""
        if uid and uid not in seen:
            seen.add(uid)
            all_uids.append(uid)

    selected: list[dict[str, Any]] = []
    for uid in sorted(all_uids):
        pdf_path = pdf_dir / f"{uid}.pdf"
        if not pdf_path.exists():
            continue
        selected.append({"doc_id": uid, "pdf_path": pdf_path})
        if len(selected) >= TATDQA_N_TARGET:
            break

    return selected


# ---------------------------------------------------------------------------
# Firewall.


def _setup_firewall() -> tuple[Any, str]:
    """Create egress-block firewall rule.  Requires elevated privileges.  Fail-closed."""
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
        print(f"  Manual cleanup: Remove-NetFirewallRule -DisplayName '{display_name}'")


# ---------------------------------------------------------------------------
# Path layout.


def _pair_dir(run_dir: Path, corpus: str, canonical_id: str, parser_id: str) -> Path:
    return run_dir / corpus / canonical_id / parser_id


def _record_path(run_dir: Path, corpus: str, canonical_id: str, parser_id: str) -> Path:
    return _pair_dir(run_dir, corpus, canonical_id, parser_id) / "execution_record.json"


def _md_path(run_dir: Path, corpus: str, canonical_id: str, parser_id: str) -> Path:
    return _pair_dir(run_dir, corpus, canonical_id, parser_id) / "parser_output.md"


def _result_path(run_dir: Path, corpus: str, canonical_id: str, parser_id: str) -> Path:
    return _pair_dir(run_dir, corpus, canonical_id, parser_id) / "track_c_result.json"


# ---------------------------------------------------------------------------
# Execution record helpers.


def _compute_pair_id(*, canonical_id: str, parser_id: str) -> str:
    return hashlib.sha256(f"{canonical_id}::{parser_id}".encode()).hexdigest()[:16]


def _build_and_write_record(
    *,
    run_dir: Path,
    corpus: str,
    canonical_id: str,
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
    git_commit: str,
    python_version: str,
    platform_string: str,
    cpu_cores: int,
    cuda_version: str | None,
    cuda_device: str | None,
    model_cache_path: str | None,
    model_cache_paths: dict[str, str],
) -> None:
    """Build a Stage1ExecutionRecord and write it to disk."""
    from benchmarks.eval_v1.smoke_b1a_7b.adapter_protocol import ParseStatus
    from benchmarks.eval_v1.stage1.record import (
        Stage1ExecutionRecord,
        write_execution_record,
    )

    is_executed = outcome.status is ParseStatus.EXECUTED
    is_cpu_only = parser_id in {"aksharamd-reference", "markitdown"}
    is_vlm = parser_id in {"marker", "docling"}

    output_bytes = outcome.markdown.encode("utf-8") if (
        is_executed and outcome.markdown
    ) else b""
    stdout_bytes = (outcome.stdout or "").encode("utf-8")
    stderr_bytes = (outcome.stderr or "").encode("utf-8")

    rec = Stage1ExecutionRecord(
        pair_id=_compute_pair_id(canonical_id=canonical_id, parser_id=parser_id),
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
        model_cache_path=None if is_cpu_only else model_cache_paths.get(parser_id),
        network_egress_blocked=True,
        pair_started_at=started_at,
        pair_finished_at=finished_at,
        wall_clock_seconds=wall_clock_seconds,
        cpu_seconds_user=cpu_user,
        cpu_seconds_system=cpu_system,
        peak_rss_bytes=peak_rss,
        peak_vram_bytes=None if is_cpu_only else peak_vram,
        cuda_events=0 if is_vlm else None,
        output_bytes=len(output_bytes),
        output_sha256=_bytes_sha256(output_bytes),
        stdout_bytes=len(stdout_bytes),
        stdout_sha256=_bytes_sha256(stdout_bytes),
        stderr_bytes=len(stderr_bytes),
        stderr_sha256=_bytes_sha256(stderr_bytes),
        exit_status="EXECUTED" if is_executed else "DEFECT",
        defect_reason=None if is_executed else outcome.defect_reason,
        normalization_version=NORMALIZATION_VERSION,
        parser_execution_contract_version=PARSER_EXECUTION_CONTRACT_VERSION,
        parser_execution_contract_config_sha256=CONTRACT_CONFIG_SHA256,
        stage1_execution_manifest_sha256=MANIFEST_SHA,
    )
    record_p = _record_path(run_dir, corpus, canonical_id, parser_id)
    write_execution_record(record_p, rec)


def _validate_resume_record(
    path: Path, *, canonical_id: str, parser_id: str
) -> tuple[bool, str]:
    """Return (skip, reason).  skip=True means the record is valid to reuse."""
    try:
        d: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
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
    if d.get("stage1_execution_manifest_sha256") != MANIFEST_SHA:
        return False, (
            f"manifest SHA mismatch: "
            f"record={str(d.get('stage1_execution_manifest_sha256'))[:16]}… "
            f"!= pinned={MANIFEST_SHA[:16]}…"
        )
    status = d.get("exit_status", "")
    if status not in {"EXECUTED", "DEFECT"}:
        return False, f"non-terminal exit_status={status!r}"
    return True, ""


# ---------------------------------------------------------------------------
# Phase 1: Parser execution.


def _run_phase1(
    *,
    run_dir: Path,
    manifest: dict[str, Any],
    qasper_items: list[dict[str, Any]],
    tatdqa_items: list[dict[str, Any]],
    resume: bool,
) -> None:
    """Run all (document, parser) pairs under a network firewall.

    Writes execution_record.json and parser_output.md for each pair.
    The markdown is captured directly from the adapter outcome so no
    second pass is needed.
    """
    from benchmarks.eval_v1.smoke_b1a_7b.adapter_protocol import ParseStatus
    from benchmarks.eval_v1.stage1.runner import build_adapters
    from benchmarks.eval_v1.smoke_b1a_7b.subprocess_runner import RealSubprocessInvoker

    model_artifact_shas: dict[str, str | None] = {
        "marker": manifest["model_artifact_shas"]["marker"],
        "docling": manifest["model_artifact_shas"]["docling"],
    }
    model_cache_paths: dict[str, str] = {
        "marker": manifest["model_cache_paths"]["marker"],
        "docling": manifest["model_cache_paths"]["docling_models"],
    }

    adapters = build_adapters(
        subprocess_invoker=RealSubprocessInvoker(),
        model_artifact_shas=model_artifact_shas,
    )

    # Flatten (corpus, doc_id, pdf_path) triples.
    corpus_items: list[tuple[str, str, Path]] = []
    for e in qasper_items:
        corpus_items.append(("qasper", e["doc_id"], e["pdf_path"]))
    for e in tatdqa_items:
        corpus_items.append(("tat_dqa", e["doc_id"], e["pdf_path"]))

    total = len(corpus_items) * len(PARSER_ORDER)
    done = 0
    n_executed = 0
    n_defect = 0
    n_skipped = 0

    print()
    print("[Phase 1] Parser execution ...")
    print(
        f"  {len(corpus_items)} documents × {len(PARSER_ORDER)} parsers "
        f"= {total} invocations"
    )
    print()
    print("  NOTE: Phase 1 requires elevated privileges for Windows Firewall.")
    print()

    # Environment snapshot (once per run).
    git_commit = _git_commit()
    python_version = sys.version.split()[0]
    platform_string = platform.platform()
    cpu_cores = _cpu_cores()
    cuda_version, _, cuda_device = _cuda_state()

    print("  Establishing network isolation (Windows Firewall) ...")
    backend, rule_name = _setup_firewall()
    print()

    t0_wall = time.monotonic()

    try:
        for corpus, canonical_id, pdf_path in corpus_items:
            try:
                pdf_bytes = pdf_path.read_bytes()
            except Exception as exc:  # noqa: BLE001
                for pid in PARSER_ORDER:
                    done += 1
                    print(
                        f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {pid} "
                        f"-> HARNESS_DEFECT (pdf read: {exc})"
                    )
                    n_defect += 1
                continue

            for parser_id in PARSER_ORDER:
                done += 1
                adapter = adapters[parser_id]
                record_p = _record_path(run_dir, corpus, canonical_id, parser_id)
                md_p = _md_path(run_dir, corpus, canonical_id, parser_id)

                # Resume: skip if existing record passes full identity validation
                # AND parser_output.md is present.
                record_is_valid = False
                if resume and record_p.exists():
                    skip, skip_reason = _validate_resume_record(
                        record_p, canonical_id=canonical_id, parser_id=parser_id
                    )
                    if skip and md_p.exists():
                        n_skipped += 1
                        existing = json.loads(record_p.read_text(encoding="utf-8"))
                        print(
                            f"  [{done}/{total}] SKIP (resume) "
                            f"{corpus}/{canonical_id[:24]} × {parser_id} "
                            f"[{existing.get('exit_status', '?')}]"
                        )
                        if existing.get("exit_status") == "EXECUTED":
                            n_executed += 1
                        else:
                            n_defect += 1
                        continue
                    elif skip and not md_p.exists():
                        # Record exists and is valid but md is missing — re-run adapter
                        # only to write the md file; skip record rewrite.
                        record_is_valid = True
                        print(
                            f"  [{done}/{total}] RE-RUN md-only "
                            f"{corpus}/{canonical_id[:24]} × {parser_id}"
                        )
                    elif not skip:
                        print(
                            f"  [{done}/{total}] RE-RUN (resume invalid: {skip_reason}) "
                            f"{corpus}/{canonical_id[:24]} × {parser_id}"
                        )

                if not record_is_valid:
                    print(
                        f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {parser_id}"
                    )

                t_pair = time.monotonic()
                started = _now_utc()

                try:
                    import psutil as _psutil
                    proc = _psutil.Process()
                    cpu_before = proc.cpu_times()
                    rss_before = proc.memory_info().rss
                except Exception:  # noqa: BLE001
                    proc = None
                    cpu_before = None
                    rss_before = 0

                outcome = adapter.compile(pdf_bytes, canonical_id)

                wall_secs = time.monotonic() - t_pair
                finished = _now_utc()

                cpu_user, cpu_sys, peak_rss = 0.0, 0.0, 0
                if proc is not None and cpu_before is not None:
                    try:
                        cpu_after = proc.cpu_times()
                        cpu_user = max(0.0, cpu_after.user - cpu_before.user)
                        cpu_sys = max(0.0, cpu_after.system - cpu_before.system)
                        peak_rss = max(0, proc.memory_info().rss - rss_before)
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

                # Write execution record only when not in md-only recovery mode.
                if not record_is_valid:
                    try:
                        _build_and_write_record(
                            run_dir=run_dir,
                            corpus=corpus,
                            canonical_id=canonical_id,
                            parser_id=parser_id,
                            adapter=adapter,
                            outcome=outcome,
                            started_at=started,
                            finished_at=finished,
                            wall_clock_seconds=wall_secs,
                            cpu_user=cpu_user,
                            cpu_system=cpu_sys,
                            peak_rss=peak_rss,
                            peak_vram=peak_vram,
                            git_commit=git_commit,
                            python_version=python_version,
                            platform_string=platform_string,
                            cpu_cores=cpu_cores,
                            cuda_version=cuda_version,
                            cuda_device=cuda_device,
                            model_cache_path=model_cache_paths.get(parser_id),
                            model_cache_paths=model_cache_paths,
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"    -> HARNESS_DEFECT (record schema): {exc}")
                        n_defect += 1
                        continue

                # Write parser_output.md for executed pairs.
                if outcome.status is ParseStatus.EXECUTED and outcome.markdown is not None:
                    md_p.parent.mkdir(parents=True, exist_ok=True)
                    md_p.write_text(outcome.markdown, encoding="utf-8")
                    n_executed += 1
                    print(
                        f"    -> EXECUTED ({wall_secs:.1f}s)  "
                        f"md={len(outcome.markdown)} chars"
                    )
                else:
                    n_defect += 1
                    print(
                        f"    -> DEFECT ({wall_secs:.1f}s)  "
                        f"reason={outcome.defect_reason}"
                    )

    finally:
        _teardown_firewall(backend, rule_name)

    total_wall = time.monotonic() - t0_wall
    print()
    print("=== PHASE 1 SUMMARY ===")
    print(f"  total_invocations : {done}")
    print(f"  n_executed        : {n_executed}")
    print(f"  n_defect          : {n_defect}")
    print(f"  n_skipped_resume  : {n_skipped}")
    print(f"  wall_clock_secs   : {total_wall:.1f}")


# ---------------------------------------------------------------------------
# EM normalization.


_PUNCT_TRANSLATE = str.maketrans("", "", string.punctuation)


def _normalize_for_em(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    lowered = text.lower()
    no_punct = lowered.translate(_PUNCT_TRANSLATE)
    return " ".join(no_punct.split())


def _compute_em(prediction: str, gold: str, *, is_unanswerable: bool) -> int:
    """Return 1 if exact match (after normalization), else 0."""
    if is_unanswerable:
        return 1 if prediction.strip().lower() == "unanswerable" else 0
    return 1 if _normalize_for_em(prediction) == _normalize_for_em(gold) else 0


def _compute_token_f1(prediction: str, gold: str) -> float:
    """Token-level F1 — official QASPER metric.

    '|'-separated spans (QASPER extractive) or values (TAT-DQA multi-span)
    are all treated as one combined token bag. '|' is stripped by
    _normalize_for_em (it's in string.punctuation), so spans are naturally
    concatenated into a single bag — matching the official QASPER evaluation.
    """
    pred_toks = _normalize_for_em(prediction).split()
    gold_toks = _normalize_for_em(gold).split()
    if not pred_toks and not gold_toks:
        return 1.0
    if not pred_toks or not gold_toks:
        return 0.0
    pred_bag = Counter(pred_toks)
    gold_bag = Counter(gold_toks)
    common = sum((pred_bag & gold_bag).values())
    if common == 0:
        return 0.0
    prec = common / len(pred_toks)
    rec = common / len(gold_toks)
    return 2 * prec * rec / (prec + rec)


_BOOLEAN_YES_RE = re.compile(r"^yes\b", re.IGNORECASE)
_BOOLEAN_NO_RE = re.compile(r"^no\b", re.IGNORECASE)


def _normalize_boolean(text: str) -> str | None:
    """Normalize free-form LLM response to 'yes' or 'no', or None if unrecognizable.

    Accepts "Yes, because...", "No, this paper...", etc. Uses word-boundary match
    so "nobody" does not match "no".
    """
    t = text.strip()
    if _BOOLEAN_YES_RE.match(t):
        return "yes"
    if _BOOLEAN_NO_RE.match(t):
        return "no"
    return None


_SCALE_MULT: dict[str, float] = {
    "hundred": 1e2,
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
}

# Handles embedded unit suffixes like "1.2M", "3.5B", "500K", "2.1 million".
_EMBEDDED_UNIT_RE = re.compile(
    r"^([-+]?[\d,]+\.?\d*)\s*(k|thousand|m|million|b|bn|billion|t|trillion)$",
    re.IGNORECASE,
)
_EMBEDDED_UNIT_MULT: dict[str, float] = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "trillion": 1e12,
}


def _to_float_numeric(text: str, scale: str | None) -> float | None:
    """Strip currency/percent, detect embedded unit suffix, apply scale, return float."""
    t = text.lower().strip()
    for ch in "$£€¥%":
        t = t.replace(ch, "")
    t = t.strip()
    # Try embedded unit suffix before stripping commas (e.g. "1.2m", "3.5 billion").
    m = _EMBEDDED_UNIT_RE.match(t)
    if m:
        num_str = m.group(1).replace(",", "")
        try:
            val = float(num_str)
        except ValueError:
            return None
        return val * _EMBEDDED_UNIT_MULT.get(m.group(2).lower(), 1.0)
    # No embedded unit: strip commas and apply external scale field.
    t = t.replace(",", "")
    try:
        val = float(t)
    except ValueError:
        return None
    return val * _SCALE_MULT.get((scale or "").lower().strip(), 1.0)


def _compute_numeric_em(prediction: str, gold: str, scale: str) -> float:
    """Numeric-normalized EM for TAT-DQA.

    Handles scale (million/thousand/…), strips currency/commas/percent.
    Gold may be multiple values joined with ' | '; returns 1.0 if any match.

    Matching rules per (pred_f, gold_f) parseability:
      - Both numeric: compare with 1e-4 relative tolerance. No string fallback.
        (String EM would strip '-' and '.', causing -5≡5 and 1.2≡12.)
      - Both non-numeric: string EM after normalization.
      - One numeric, one not: no match.
    """
    gold_spans = [g.strip() for g in gold.split("|")]
    pred_f = _to_float_numeric(prediction, scale)

    for g in gold_spans:
        g_f = _to_float_numeric(g, scale)
        if pred_f is not None and g_f is not None:
            # Both parsed as numbers: numeric comparison only.
            tolerance = 1e-4 * max(1.0, abs(g_f))
            if abs(pred_f - g_f) <= tolerance:
                return 1.0
        elif pred_f is None and g_f is None:
            # Both non-numeric (e.g. month names, named entities): string EM.
            if _normalize_for_em(prediction) == _normalize_for_em(g):
                return 1.0
        # Mixed numeric/non-numeric: no match.
    return 0.0


def _compute_primary_score(
    prediction: str,
    gold: str,
    answer_type: str,
    corpus: str,
    scale: str = "",
) -> float:
    """Corpus-appropriate primary score for one QA pair.

    QASPER  : token F1 for extractive/abstractive; EM for boolean/unanswerable.
    TAT-DQA : numeric EM for numeric/arithmetic/count; token F1 for multi-span;
              EM for unanswerable.
    """
    if answer_type == "unanswerable":
        return float(_compute_em(prediction, gold, is_unanswerable=True))

    if corpus == "qasper":
        if answer_type == "boolean":
            pred_b = _normalize_boolean(prediction)
            gold_b = _normalize_boolean(gold)
            if pred_b is not None and gold_b is not None:
                return 1.0 if pred_b == gold_b else 0.0
            # Fallback: LLM or gold unparseable — use string EM.
            return float(_compute_em(prediction, gold, is_unanswerable=False))
        # abstractive, extractive: token F1 (official QASPER metric)
        return _compute_token_f1(prediction, gold)

    if corpus == "tat_dqa":
        if answer_type in ("numeric", "arithmetic", "count"):
            return _compute_numeric_em(prediction, gold, scale)
        return _compute_token_f1(prediction, gold)

    return float(_compute_em(prediction, gold, is_unanswerable=False))


# ---------------------------------------------------------------------------
# QASPER gold-text arm helpers.


def _extract_qasper_gold_text(paper: dict[str, Any]) -> str:
    """Convert a QASPER paper's full_text field to plain markdown.

    Sections become ## headings; paragraphs become plain text blocks.
    Returns an empty string if full_text is absent.
    """
    title = paper.get("title") or ""
    sections = paper.get("full_text") or []
    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    for section in sections:
        heading = (section.get("section_name") or "").strip()
        if heading:
            parts.append(f"\n## {heading}")
        for para in section.get("paragraphs") or []:
            text = (para or "").strip()
            if text:
                parts.append(f"\n{text}")
    return "\n".join(parts)


def _get_qasper_gold_text(canonical_id: str) -> str | None:
    """Return extracted gold markdown for a QASPER arxiv_id, or None."""
    dev_json = ROOT / ".cache" / "qasper" / "qasper-dev-v0.3.json"
    if not dev_json.exists():
        return None
    index: dict[str, Any] = json.loads(dev_json.read_text(encoding="utf-8"))
    paper = index.get(canonical_id)
    if paper is None:
        return None
    text = _extract_qasper_gold_text(paper)
    return text if text.strip() else None


# ---------------------------------------------------------------------------
# Phase 2: LLM evaluation.


def _call_llm(client: Any, prompt: str) -> str:
    """Call claude-sonnet-4-6 at temperature=0.0.  Returns the response text."""
    assert TEMPERATURE == 0.0, "Temperature must be 0.0 — invariant violated"
    message = client.messages.create(
        model=MODEL_ID,
        max_tokens=512,
        temperature=TEMPERATURE,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _write_track_c_result(
    path: Path,
    *,
    corpus: str,
    canonical_id: str,
    parser_id: str,
    execution_status: str,
    n_qa_pairs: int,
    n_answered: int,
    n_llm_errors: int = 0,
    em_score: float | None,
    primary_score: float | None = None,
    primary_metric: str | None = None,
    readiness_score: int | None,
    warning_codes: list[str],
    llm_evaluated: bool,
    input_sha256: str | None = None,
    qa_results: list[dict[str, Any]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "schema_version": "3",
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "scoring_contract_id": SCORING_CONTRACT_ID,
        "corpus": corpus,
        "canonical_id": canonical_id,
        "parser_id": parser_id,
        "execution_status": execution_status,
        "n_qa_pairs": n_qa_pairs,
        "n_answered": n_answered,
        "n_llm_errors": n_llm_errors,
        "em_score": em_score,
        "primary_score": primary_score,
        "primary_metric": primary_metric,
        "readiness_score": readiness_score,
        "warning_codes": warning_codes,
        "prompt_sha256": PROMPT_SHA256,
        "model": MODEL_ID,
        "input_sha256": input_sha256,
        "llm_evaluated": llm_evaluated,
        "qa_results": qa_results if qa_results is not None else [],
    }
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def _run_phase2(
    *,
    run_dir: Path,
    prompt_template: str,
    qasper_items: list[dict[str, Any]],
    tatdqa_items: list[dict[str, Any]],
    qasper_adapter: Any,
    tatdqa_adapter: Any,
    resume: bool,
) -> None:
    """Phase 2: LLM evaluation over all executed (doc, parser) pairs."""
    try:
        import anthropic
    except ImportError as exc:
        raise SystemExit(
            "ABORT: anthropic SDK not installed. Run: pip install anthropic"
        ) from exc

    client = anthropic.Anthropic()

    all_items: list[tuple[str, dict[str, Any], Any]] = [
        ("qasper", e, qasper_adapter) for e in qasper_items
    ] + [
        ("tat_dqa", e, tatdqa_adapter) for e in tatdqa_items
    ]

    total = len(all_items) * len(EVAL_PARSER_ORDER)
    done = 0
    n_evaluated = 0
    n_skipped = 0
    consecutive_infra_errors = 0

    print()
    print(f"[Phase 2] LLM evaluation ({MODEL_ID}, temp={TEMPERATURE}) ...")
    print()

    for corpus, entry, adapter in all_items:
        canonical_id = entry["doc_id"]

        gt = adapter.ingest_ground_truth(canonical_id)
        if gt is None:
            for parser_id in EVAL_PARSER_ORDER:
                done += 1
                print(
                    f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {parser_id} "
                    f"-> SKIP (no ground truth)"
                )
            continue

        qa_pairs: list[dict[str, str]] = gt.data.get("qa_pairs", [])
        if not qa_pairs:
            for parser_id in EVAL_PARSER_ORDER:
                done += 1
                print(
                    f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {parser_id} "
                    f"-> SKIP (0 QA pairs)"
                )
            continue

        for parser_id in EVAL_PARSER_ORDER:
            done += 1
            is_gold_arm = parser_id == GOLD_ARM_ID
            result_p = _result_path(run_dir, corpus, canonical_id, parser_id)
            md_p = _md_path(run_dir, corpus, canonical_id, parser_id)
            record_p = _record_path(run_dir, corpus, canonical_id, parser_id)

            # Gold arm only exists for QASPER (TAT-DQA has no full_text).
            if is_gold_arm and corpus != "qasper":
                print(
                    f"  [{done}/{total}] SKIP "
                    f"{corpus}/{canonical_id[:24]} × {parser_id} (no full_text for {corpus})"
                )
                continue

            # Compute current input hash from the markdown file if it already exists.
            # Uses read_text().encode() to match how the stored hash is produced,
            # ensuring universal-newline normalisation on Windows doesn't cause mismatches.
            current_input_sha256: str | None = (
                _bytes_sha256(md_p.read_text(encoding="utf-8").encode("utf-8"))
                if md_p.exists()
                else None
            )

            # Resume: skip if already evaluated AND provenance is fully compatible.
            # _is_phase2_result_compatible checks: llm_evaluated, metric_schema_version,
            # prompt_sha256, scoring_contract_id, and input_sha256 (fail closed).
            # n_skipped is set outside the try block so a mid-block exception cannot
            # increment it without the corresponding continue.
            _skip_resumed = False
            if resume and result_p.exists():
                try:
                    existing = json.loads(result_p.read_text(encoding="utf-8"))
                    if _is_phase2_result_compatible(existing, current_input_sha256):
                        em = existing.get("em_score")
                        em_str = f"{em:.3f}" if em is not None else "N/A"
                        print(
                            f"  [{done}/{total}] SKIP (resume) "
                            f"{corpus}/{canonical_id[:24]} x {parser_id} "
                            f"[em={em_str}]"
                        )
                        _skip_resumed = True
                    elif existing.get("llm_evaluated"):
                        # Only log REJECT for complete records; partial checkpoints
                        # (llm_evaluated=False) are silently resumed via partial_qa below.
                        mismatches = []
                        if existing.get("metric_schema_version") != METRIC_SCHEMA_VERSION:
                            mismatches.append(
                                f"metric_schema_version "
                                f"{existing.get('metric_schema_version')!r}->{METRIC_SCHEMA_VERSION!r}"
                            )
                        if existing.get("prompt_sha256") != PROMPT_SHA256:
                            mismatches.append("prompt_sha256")
                        if existing.get("scoring_contract_id") != SCORING_CONTRACT_ID:
                            mismatches.append(
                                f"scoring_contract_id "
                                f"{existing.get('scoring_contract_id')!r}->{SCORING_CONTRACT_ID!r}"
                            )
                        if existing.get("input_sha256") != current_input_sha256:
                            mismatches.append("input_sha256")
                        print(
                            f"  [{done}/{total}] REJECT stale "
                            f"{corpus}/{canonical_id[:24]} x {parser_id} "
                            f"({'; '.join(mismatches) or 'unknown'}) -- re-evaluating"
                        )
                except Exception:  # noqa: BLE001
                    pass
            if _skip_resumed:
                n_skipped += 1
                continue

            if is_gold_arm:
                # Generate and cache gold markdown from QASPER JSON.
                if not md_p.exists():
                    gold_md = _get_qasper_gold_text(canonical_id)
                    if gold_md is None:
                        print(
                            f"  [{done}/{total}] SKIP "
                            f"{corpus}/{canonical_id[:24]} × {parser_id} (no full_text in JSON)"
                        )
                        continue
                    md_p.parent.mkdir(parents=True, exist_ok=True)
                    md_p.write_text(gold_md, encoding="utf-8")
            else:
                # Normal parser: check execution status from Phase 1 record.
                if not record_p.exists():
                    print(
                        f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {parser_id} "
                        f"-> SKIP (no execution record — run Phase 1 first)"
                    )
                    continue

                exec_rec = json.loads(record_p.read_text(encoding="utf-8"))
                if exec_rec.get("exit_status") != "EXECUTED":
                    defect_reason = exec_rec.get("defect_reason", "?")
                    print(
                        f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {parser_id} "
                        f"-> DEFECT (parser: {defect_reason})"
                    )
                    _write_track_c_result(
                        result_p,
                        corpus=corpus,
                        canonical_id=canonical_id,
                        parser_id=parser_id,
                        execution_status="DEFECT",
                        n_qa_pairs=len(qa_pairs),
                        n_answered=0,
                        em_score=None,
                        readiness_score=None,
                        warning_codes=[],
                        llm_evaluated=False,
                    )
                    continue

                if not md_p.exists():
                    print(
                        f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {parser_id} "
                        f"-> SKIP (parser_output.md missing — re-run Phase 1)"
                    )
                    continue

            markdown = md_p.read_text(encoding="utf-8")
            input_sha256 = _bytes_sha256(markdown.encode("utf-8"))
            t_start = time.monotonic()
            n_correct = 0
            primary_sum = 0.0
            n_answered = 0
            n_llm_errors = 0
            qa_results: list[dict[str, Any]] = []

            # Corpus-level primary metric label.
            primary_metric = "token_f1" if corpus == "qasper" else "numeric_em"

            # Load any compatible checkpoint, including an older record that was
            # incorrectly finalized despite infrastructure errors.  Reuse only
            # answered/no-gold rows; llm_error rows are deliberately retried.
            partial_qa: dict[int, dict[str, Any]] = {}
            if result_p.exists():
                try:
                    _chk = json.loads(result_p.read_text(encoding="utf-8"))
                    partial_qa = _restorable_phase2_qa_results(
                        _chk, input_sha256
                    )
                except Exception:  # noqa: BLE001
                    pass

            for qa_idx, qa in enumerate(qa_pairs):
                question = qa.get("question", "")
                gold = qa.get("gold_answer", "")
                answer_type = qa.get("answer_type", "")
                scale = qa.get("scale", "")
                is_unanswerable = answer_type == "unanswerable"

                # gold_annotations carries all annotator answers (QASPER).
                # For TAT-DQA there is one annotation per pair.
                annotations: list[dict[str, str]] = qa.get("gold_annotations") or [
                    {"gold_answer": gold, "answer_type": answer_type}
                ]

                # Restore from partial checkpoint — skip the LLM call for this question.
                if qa_idx in partial_qa:
                    cached = partial_qa[qa_idx]
                    qa_results.append(cached)
                    if cached.get("status") == "answered":
                        n_correct += cached.get("em_score") or 0.0
                        primary_sum += cached.get("primary_score") or 0.0
                        n_answered += 1
                    continue

                # Skip pairs with no usable gold.
                if not is_unanswerable and not gold:
                    qa_results.append({
                        "question_id": qa_idx,
                        "question": question,
                        "answer_type": answer_type,
                        "scale": scale,
                        "gold_annotations": annotations,
                        "status": "no_gold",
                        "prediction": None,
                        "prediction_sha256": None,
                        "em_score": None,
                        "primary_score": None,
                    })
                    continue

                prompt_text = (
                    prompt_template
                    .replace("{document}", markdown)
                    .replace("{question}", question)
                )
                try:
                    prediction = _call_llm(client, prompt_text)
                except Exception as exc:  # noqa: BLE001
                    err_msg = str(exc)
                    print(f"    [LLM INFRA ERROR] {err_msg} -- pair skipped (not counted as wrong)")
                    n_llm_errors += 1
                    qa_results.append({
                        "question_id": qa_idx,
                        "question": question,
                        "answer_type": answer_type,
                        "scale": scale,
                        "gold_annotations": annotations,
                        "status": "llm_error",
                        "error": err_msg,
                        "prediction": None,
                        "prediction_sha256": None,
                        "em_score": None,
                        "primary_score": None,
                    })
                    # Persist the failure for audit, but keep the document incomplete
                    # so resume retries this question rather than treating it as wrong.
                    _write_track_c_result(
                        result_p,
                        corpus=corpus,
                        canonical_id=canonical_id,
                        parser_id=parser_id,
                        execution_status="EXECUTED",
                        n_qa_pairs=len(qa_pairs),
                        n_answered=n_answered,
                        n_llm_errors=n_llm_errors,
                        em_score=(n_correct / n_answered if n_answered else None),
                        primary_score=(primary_sum / n_answered if n_answered else None),
                        primary_metric=primary_metric,
                        readiness_score=None,
                        warning_codes=[],
                        llm_evaluated=False,
                        input_sha256=input_sha256,
                        qa_results=qa_results,
                    )
                    consecutive_infra_errors += 1
                    if _is_terminal_llm_error(exc):
                        raise SystemExit(
                            "ABORT: non-retryable LLM provider error; checkpoint saved.\n"
                            f"  {err_msg}"
                        ) from exc
                    if consecutive_infra_errors >= 3:
                        raise SystemExit(
                            "ABORT: 3 consecutive LLM infrastructure errors; "
                            "checkpoint saved."
                        ) from exc
                    continue  # infrastructure failure: do not count as answered

                # Max over annotators — one LLM call, best gold match wins.
                em = max(
                    _compute_em(
                        prediction,
                        ann["gold_answer"],
                        is_unanswerable=ann["answer_type"] == "unanswerable",
                    )
                    for ann in annotations
                )
                primary = max(
                    _compute_primary_score(
                        prediction, ann["gold_answer"], ann["answer_type"], corpus, scale
                    )
                    for ann in annotations
                )
                n_correct += em
                primary_sum += primary
                n_answered += 1
                consecutive_infra_errors = 0
                qa_results.append({
                    "question_id": qa_idx,
                    "question": question,
                    "answer_type": answer_type,
                    "scale": scale,
                    "gold_annotations": annotations,
                    "status": "answered",
                    "prediction": prediction,
                    "prediction_sha256": _bytes_sha256(prediction.encode("utf-8")),
                    "em_score": em,
                    "primary_score": primary,
                })

                # Checkpoint after each answered question so a crash does not lose
                # completed paid LLM calls. llm_evaluated=False marks this as partial;
                # the final write below sets it True.
                _write_track_c_result(
                    result_p,
                    corpus=corpus,
                    canonical_id=canonical_id,
                    parser_id=parser_id,
                    execution_status="EXECUTED",
                    n_qa_pairs=len(qa_pairs),
                    n_answered=n_answered,
                    n_llm_errors=n_llm_errors,
                    em_score=n_correct / n_answered,
                    primary_score=primary_sum / n_answered,
                    primary_metric=primary_metric,
                    readiness_score=None,
                    warning_codes=[],
                    llm_evaluated=False,
                    input_sha256=input_sha256,
                    qa_results=qa_results,
                )

            elapsed = time.monotonic() - t_start
            em_score = n_correct / n_answered if n_answered > 0 else None
            primary_score = primary_sum / n_answered if n_answered > 0 else None
            n_evaluated += 1

            ps_fmt = f"{primary_score:.3f}" if primary_score is not None else "N/A"
            em_fmt = f"{em_score:.3f}" if em_score is not None else "N/A"
            print(
                f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {parser_id} "
                f"-> {primary_metric}={ps_fmt} em={em_fmt} "
                f"({n_answered} pairs) ({elapsed:.1f}s)"
            )

            _write_track_c_result(
                result_p,
                corpus=corpus,
                canonical_id=canonical_id,
                parser_id=parser_id,
                execution_status="EXECUTED",
                n_qa_pairs=len(qa_pairs),
                n_answered=n_answered,
                n_llm_errors=n_llm_errors,
                em_score=em_score,
                primary_score=primary_score,
                primary_metric=primary_metric,
                readiness_score=None,   # filled in Phase 3
                warning_codes=[],       # filled in Phase 3
                llm_evaluated=(n_llm_errors == 0),
                input_sha256=input_sha256,
                qa_results=qa_results,
            )

    print()
    print(f"  Phase 2 complete: {n_evaluated} LLM-evaluated, {n_skipped} skipped (resume)")


# ---------------------------------------------------------------------------
# Phase 3: Readiness scoring.


def _score_markdown(markdown: str) -> tuple[int | None, list[str]]:
    """Score parser Markdown output through the AksharaMD Compiler.

    SCORING CONTRACT (V1 declared limitation):
      This function scores the parser's Markdown output as a Markdown document,
      NOT the original source PDF. Consequences:
        - Format baseline is 95 (Markdown), NOT 87 (PDF, frozen in manifest §1).
        - Source/geometry detectors (W_TABLE_MISSING, W_DROPPED_CONTENT) that
          compare parsed output against source content are NOT activated, because
          no source PDF is supplied. These detectors are UNMEASURED in V1.
        - All observed readiness scores in Track C are on the Markdown-output
          scale (baseline 95), not the PDF scale (baseline 87).
      This is an explicit declaration of scope, not a silent limitation.
      Resolution B (source+candidate scoring) is deferred to V2.

    Returns (score, warning_codes). Returns (None, []) on any error.
    """
    try:
        from aksharamd.compiler import Compiler
        from aksharamd.scoring.models import SCORING_POLICY_VERSION

        if SCORING_POLICY_VERSION != SCORING_POLICY_VERSION_REQUIRED:
            raise RuntimeError(
                f"SCORING_POLICY_VERSION mismatch: "
                f"installed={SCORING_POLICY_VERSION!r} "
                f"required={SCORING_POLICY_VERSION_REQUIRED!r}"
            )

        with tempfile.NamedTemporaryFile(
            suffix=".md", mode="w", encoding="utf-8", delete=False
        ) as fh:
            fh.write(markdown)
            tmp_path = fh.name

        try:
            compiler = Compiler(
                output_dir=tempfile.mkdtemp(prefix="trackc_score_"),
            )
            _text, ctx = compiler.compile_to_string(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        manifest = getattr(ctx, "manifest", None)
        if manifest is None:
            return None, []

        score: int | None = getattr(manifest, "readiness_score", None)
        warning_codes: list[str] = list(getattr(manifest, "warning_codes", []) or [])
        return score, warning_codes

    except Exception as exc:  # noqa: BLE001
        print(f"    [readiness ERROR] {exc}")
        return None, []


def _run_phase3(
    *,
    run_dir: Path,
    qasper_items: list[dict[str, Any]],
    tatdqa_items: list[dict[str, Any]],
    resume: bool,
) -> None:
    """Phase 3: compute readiness_score + warning_codes for all executed pairs."""
    all_items: list[tuple[str, dict[str, Any]]] = (
        [("qasper", e) for e in qasper_items]
        + [("tat_dqa", e) for e in tatdqa_items]
    )

    total = len(all_items) * len(EVAL_PARSER_ORDER)
    done = 0
    n_scored = 0
    n_skipped = 0

    print()
    print("[Phase 3] Readiness scoring ...")
    print()

    for corpus, entry in all_items:
        canonical_id = entry["doc_id"]

        for parser_id in EVAL_PARSER_ORDER:
            done += 1
            md_p = _md_path(run_dir, corpus, canonical_id, parser_id)
            result_p = _result_path(run_dir, corpus, canonical_id, parser_id)

            if not md_p.exists():
                print(
                    f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {parser_id} "
                    f"-> SKIP (no parser_output.md)"
                )
                continue

            # Resume: skip if readiness_score is already populated.
            if resume and result_p.exists():
                try:
                    existing = json.loads(result_p.read_text(encoding="utf-8"))
                    if existing.get("readiness_score") is not None:
                        n_skipped += 1
                        print(
                            f"  [{done}/{total}] SKIP (resume) "
                            f"{corpus}/{canonical_id[:24]} × {parser_id} "
                            f"[score={existing.get('readiness_score')}]"
                        )
                        continue
                except Exception:  # noqa: BLE001
                    pass

            markdown = md_p.read_text(encoding="utf-8")
            t_start = time.monotonic()
            readiness_score, warning_codes = _score_markdown(markdown)
            elapsed = time.monotonic() - t_start

            n_scored += 1
            print(
                f"  [{done}/{total}] {corpus}/{canonical_id[:24]} × {parser_id} "
                f"-> score={readiness_score} warn={warning_codes} ({elapsed:.1f}s)"
            )

            # Update result file if it exists; create a minimal one if not.
            if result_p.exists():
                try:
                    rec = json.loads(result_p.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    rec = {}
                rec["readiness_score"] = readiness_score
                rec["warning_codes"] = warning_codes
                result_p.write_text(json.dumps(rec, indent=2), encoding="utf-8")
            else:
                _write_track_c_result(
                    result_p,
                    corpus=corpus,
                    canonical_id=canonical_id,
                    parser_id=parser_id,
                    execution_status="EXECUTED",
                    n_qa_pairs=0,
                    n_answered=0,
                    em_score=None,
                    primary_score=None,
                    primary_metric=None,
                    readiness_score=readiness_score,
                    warning_codes=warning_codes,
                    llm_evaluated=False,
                )

    print()
    print(f"  Phase 3 complete: {n_scored} scored, {n_skipped} skipped (resume)")


# ---------------------------------------------------------------------------
# Summary.


def _print_summary(
    run_dir: Path,
    qasper_items: list[dict[str, Any]],
    tatdqa_items: list[dict[str, Any]],
) -> None:
    def _count(corpus: str, items: list[dict[str, Any]]) -> dict[str, int]:
        n_exec = 0
        n_llm = 0
        n_scored = 0
        for entry in items:
            cid = entry["doc_id"]
            for pid in EVAL_PARSER_ORDER:
                rp = _result_path(run_dir, corpus, cid, pid)
                if not rp.exists():
                    continue
                try:
                    rec = json.loads(rp.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("execution_status") == "EXECUTED":
                    n_exec += 1
                if rec.get("llm_evaluated"):
                    n_llm += 1
                if rec.get("readiness_score") is not None:
                    n_scored += 1
        return {"executed": n_exec, "llm_evaluated": n_llm, "scored": n_scored}

    q = _count("qasper", qasper_items)
    t = _count("tat_dqa", tatdqa_items)

    print()
    print("=== SUMMARY ===")
    print(
        f"  QASPER          : {q['executed']} executed, "
        f"{q['llm_evaluated']} LLM-evaluated, {q['scored']} readiness-scored"
    )
    print(
        f"  TAT-DQA         : {t['executed']} executed, "
        f"{t['llm_evaluated']} LLM-evaluated, {t['scored']} readiness-scored"
    )
    print("  MMLongBench-Doc : NOT_EXECUTABLE (no adapter)")
    print(f"  run_dir         : {run_dir}")


# ---------------------------------------------------------------------------
# Entry point.


def run(*, phases: set[int], resume: bool = True) -> None:
    print("=== TRACK C (Consequential Validity) ===")
    print(f"  Corpora: QASPER (n={QASPER_N_TARGET}) + TAT-DQA (n={TATDQA_N_TARGET})")
    print("  MMLongBench-Doc: NOT_EXECUTABLE (no adapter) — skipping")
    print(f"  phases  : {sorted(phases)}")
    print(f"  resume  : {resume}")
    print()

    print("[1/3] Verifying execution manifest ...")
    manifest = _verify_manifest()
    run_dir = ROOT / manifest["run_roots"]["track_c"]
    print(
        f"  OK — admission_batch_status=PASSED, "
        f"scoring_policy_version={manifest['scoring_policy_version']}"
    )
    print(f"  run_dir : {run_dir}")
    print()

    print("[2/3] Verifying prompt SHA-256 ...")
    prompt_template = _verify_prompt()
    print(f"  OK — prompt SHA-256 verified: {PROMPT_SHA256[:24]}...")
    print()

    print("[3/3] Building corpus items ...")
    qasper_items = _build_qasper_items()
    tatdqa_items = _build_tatdqa_items()

    if not qasper_items:
        print(
            "  WARNING: No QASPER PDFs found at .cache/qasper/\n"
            "  Download qasper-train-dev-v0.3.tgz from https://allenai.org/data/qasper\n"
            "  and place it at .cache/qasper/ together with individual arxiv PDFs."
        )
    else:
        print(f"  QASPER  : {len(qasper_items)} documents (n_target={QASPER_N_TARGET})")

    if not tatdqa_items:
        print(
            "  WARNING: No TAT-DQA PDFs found at .cache/tat_dqa/tat_docs/dev/\n"
            "  Download tatdqa_docs_dev.zip from "
            "https://github.com/NExTplusplus/TAT-DQA/tree/master/dataset_raw\n"
            "  and extract to .cache/tat_dqa/tat_docs/dev/"
        )
    else:
        print(f"  TAT-DQA : {len(tatdqa_items)} documents (n_target={TATDQA_N_TARGET})")

    if not qasper_items and not tatdqa_items:
        print()
        print("  ABORT: No corpus data available.  Acquire data and retry.")
        return

    print()
    run_dir.mkdir(parents=True, exist_ok=True)

    if 1 in phases:
        _run_phase1(
            run_dir=run_dir,
            manifest=manifest,
            qasper_items=qasper_items,
            tatdqa_items=tatdqa_items,
            resume=resume,
        )

    if 2 in phases:
        from benchmarks.eval_v1.adapters.qasper_v1 import QasperV1Adapter
        from benchmarks.eval_v1.adapters.tat_dqa_v1 import TatDqaV1Adapter

        qasper_adapter = QasperV1Adapter(
            doc_id_to_path={e["doc_id"]: e["pdf_path"] for e in qasper_items}
        ) if qasper_items else _NullAdapter()
        tatdqa_adapter = TatDqaV1Adapter(
            doc_id_to_path={e["doc_id"]: e["pdf_path"] for e in tatdqa_items}
        ) if tatdqa_items else _NullAdapter()

        _run_phase2(
            run_dir=run_dir,
            prompt_template=prompt_template,
            qasper_items=qasper_items,
            tatdqa_items=tatdqa_items,
            qasper_adapter=qasper_adapter,
            tatdqa_adapter=tatdqa_adapter,
            resume=resume,
        )

    if 3 in phases:
        _run_phase3(
            run_dir=run_dir,
            qasper_items=qasper_items,
            tatdqa_items=tatdqa_items,
            resume=resume,
        )

    _print_summary(run_dir, qasper_items, tatdqa_items)


class _NullAdapter:
    """Stub adapter that returns no ground truth (used when corpus is unavailable)."""

    def ingest_ground_truth(self, doc_id: str) -> None:
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-run all invocations even if validated records exist.",
    )
    p.add_argument(
        "--phase",
        type=int,
        choices=[1, 2, 3],
        help=(
            "Run only a specific phase: 1=parser execution (firewall), "
            "2=LLM evaluation, 3=readiness scoring.  "
            "Default: all three phases in sequence."
        ),
    )
    args = p.parse_args(argv)
    phases: set[int] = {args.phase} if args.phase else {1, 2, 3}
    run(phases=phases, resume=not args.no_resume)
    return 0


if __name__ == "__main__":
    sys.exit(main())
