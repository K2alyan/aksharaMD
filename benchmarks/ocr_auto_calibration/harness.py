"""Orchestrator for the OCR Auto Policy v1 calibration harness.

Runs each corpus document through three treatments — ``tesseract``,
``unlimited_ocr``, ``auto`` — records per-run metrics, and produces the
top-level :class:`~.schema.RunReport`.

The compile invocation shape mirrors PR 100's
``tests/test_ocr_backends/test_auto_selector_dispatch.py`` — the CLI is
invoked in-process via ``subprocess.run(['aksharamd', 'compile', ...])``.

Two run modes:

* ``dry_run=True`` — mock the subprocess and manifest reads. Produces a
  schema-conformant report with fabricated numbers; used by unit tests and
  for validating report/queue emission without a real GPU.
* ``dry_run=False`` — actual subprocess calls, real manifest parsing, and
  optional VRAM sampling via ``nvidia-smi`` if it is on PATH.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess  # nosec B404 - controlled invocation of aksharamd CLI
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import cache as cache_mod
from . import corpus as corpus_mod
from .corpus import CorpusEntry, enumerate_corpus
from .metrics import (
    detect_repetition,
    source_page_provenance_complete,
    structural_metrics,
)
from .preference import build_document_summary
from .schema import (
    HARNESS_SCHEMA_VERSION,
    DocumentSummary,
    RunKey,
    RunReport,
    RunResult,
    Treatment,
)

# ── Per-treatment timeouts ────────────────────────────────────────────

_TIMEOUTS: dict[Treatment, int] = {
    "tesseract": 300,  # 5 min
    "unlimited_ocr": 3600,  # 1 h
    "auto": 3600,  # 1 h
}


# ── Stderr sanitiser ──────────────────────────────────────────────────

_SECRET_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9_]+"),
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"X-Amz-[^&\s]+"),
    re.compile(r"sig=[^&\s]+", re.IGNORECASE),
    re.compile(r"token=[^&\s]+", re.IGNORECASE),
]


def _sanitize_stderr(text: str, max_bytes: int = 2048) -> str:
    """Scrub known secret shapes and truncate to the last ``max_bytes`` bytes."""
    scrubbed = text
    for pattern in _SECRET_PATTERNS:
        scrubbed = pattern.sub("[REDACTED]", scrubbed)
    encoded = scrubbed.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        encoded = encoded[-max_bytes:]
    return encoded.decode("utf-8", errors="replace")


# ── VRAM sampler ──────────────────────────────────────────────────────


class _VramSampler:
    """Background thread that polls ``nvidia-smi`` for VRAM usage.

    Falls back to ``None`` peak if ``nvidia-smi`` is not on PATH — the
    calibration harness must run on machines without an NVIDIA driver, which
    is exactly the case for Tesseract-only runs.
    """

    def __init__(self, poll_interval_s: float = 1.0) -> None:
        self._poll_interval_s = poll_interval_s
        self._peak_mib: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._available = shutil.which("nvidia-smi") is not None

    def start(self) -> None:
        if not self._available:
            return
        self._peak_mib = 0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> int | None:
        if not self._available or self._thread is None:
            return None
        self._stop.set()
        self._thread.join(timeout=5)
        return self._peak_mib

    def _loop(self) -> None:
        while not self._stop.wait(self._poll_interval_s):
            try:
                proc = subprocess.run(  # nosec B603 B607 - fixed command w/ CSV args
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode != 0:
                    continue
                first_line = proc.stdout.strip().splitlines()[0].strip()
                mib = int(first_line)
                if self._peak_mib is None or mib > self._peak_mib:
                    self._peak_mib = mib
            except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
                # nvidia-smi returned garbage or hung; skip this sample.
                continue


# ── Compile-invocation shape ──────────────────────────────────────────


@dataclass
class _CompileOutcome:
    exit_status: int
    runtime_seconds: float
    stderr_tail: str
    peak_vram_mib: int | None
    markdown: str
    manifest: dict[str, Any]
    output_sha256: str | None


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_output_artifacts(out_dir: Path) -> tuple[str, dict[str, Any], str | None]:
    """Locate the produced ``document.md`` + ``manifest.json`` under *out_dir*.

    The CLI writes output under ``<out_dir>/<stem>/document.md``. When several
    outputs exist we take the newest by mtime.
    """
    md_candidates = sorted(out_dir.rglob("document.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    manifest_candidates = sorted(out_dir.rglob("manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not md_candidates or not manifest_candidates:
        return "", {}, None
    md_text = md_candidates[0].read_text(encoding="utf-8", errors="replace")
    with manifest_candidates[0].open("r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    return md_text, manifest, _sha256_of_file(md_candidates[0])


def _invoke_compile(
    document_path: Path,
    out_dir: Path,
    treatment: Treatment,
    *,
    executor: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> _CompileOutcome:
    """Execute one ``aksharamd compile`` invocation for *treatment*."""
    exec_fn = executor or subprocess.run
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aksharamd",
        "compile",
        str(document_path),
        "-o",
        str(out_dir),
        "--ocr-backend",
        treatment,
        "--json",
    ]

    sampler = _VramSampler()
    sampler.start()
    started = time.monotonic()
    try:
        try:
            proc = exec_fn(  # nosec B603 - controlled arg list, no shell
                cmd,
                capture_output=True,
                text=True,
                timeout=_TIMEOUTS[treatment],
            )
            exit_status = proc.returncode
            stderr_tail = _sanitize_stderr(proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            exit_status = 124  # conventional "timeout" exit code
            stderr_tail = _sanitize_stderr((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        except (OSError, FileNotFoundError) as exc:
            exit_status = 127
            stderr_tail = _sanitize_stderr(str(exc))
    finally:
        runtime_seconds = max(0.0, time.monotonic() - started)
        peak_vram_mib = sampler.stop() if treatment in ("unlimited_ocr", "auto") else None

    md_text, manifest, md_sha = _read_output_artifacts(out_dir)
    return _CompileOutcome(
        exit_status=exit_status,
        runtime_seconds=runtime_seconds,
        stderr_tail=stderr_tail,
        peak_vram_mib=peak_vram_mib,
        markdown=md_text,
        manifest=manifest,
        output_sha256=md_sha,
    )


# ── Dry-run stubs ─────────────────────────────────────────────────────


def _dry_run_outcome(document_path: Path, treatment: Treatment) -> _CompileOutcome:
    """Return a fabricated but schema-conformant outcome for smoke tests."""
    md = (
        f"# Dry-run output for {document_path.name}\n\n"
        f"Treatment: **{treatment}**.\n\n"
        f"Paragraph one of dry-run fixture content.\n\n"
        f"Paragraph two of dry-run fixture content.\n"
    )
    manifest = {
        "readiness_score": 82,
        "quality_band": "Ready",
        "warning_codes": [],
        "pages": [{"page_index": 0}, {"page_index": 1}],
        "ocr_backend_requested": treatment,
        "ocr_backend_selected": (
            "tesseract" if treatment != "unlimited_ocr" else "unlimited_ocr"
        ),
        "ocr_auto_decision": (
            {
                "preferred_backend": "tesseract",
                "fallback_occurred": False,
                "fallback_reason": None,
            }
            if treatment == "auto"
            else None
        ),
        "stage_timings": {},
    }
    return _CompileOutcome(
        exit_status=0,
        runtime_seconds=0.42,
        stderr_tail="",
        peak_vram_mib=None if treatment == "tesseract" else 2048,
        markdown=md,
        manifest=manifest,
        output_sha256=hashlib.sha256(md.encode("utf-8")).hexdigest(),
    )


# ── Result assembly ───────────────────────────────────────────────────


def _extract_page_count(manifest: dict[str, Any]) -> int:
    """Read total page count from a compile manifest.

    Manifest schema 1.4 stores ``pages`` as an integer (not a list),
    with an optional ``total_pages`` alias. Handles both shapes plus
    the legacy list form defensively.
    """
    for field in ("total_pages", "pages"):
        value = manifest.get(field)
        if isinstance(value, int):
            return value
        if isinstance(value, list):
            return len(value)
    return 0


def _decision_field(manifest: dict[str, Any], field: str) -> Any:
    decision = manifest.get("ocr_auto_decision") or {}
    if isinstance(decision, dict):
        return decision.get(field)
    return None


def _build_run_result(
    *,
    entry: CorpusEntry,
    treatment: Treatment,
    key: RunKey,
    outcome: _CompileOutcome,
) -> RunResult:
    md = outcome.markdown
    manifest = outcome.manifest
    metrics = structural_metrics(md, manifest)
    max_repeat, rep_flag = detect_repetition(md)
    expected_pages = _extract_page_count(manifest)
    provenance_ok = source_page_provenance_complete(manifest, expected_pages)

    return RunResult(
        key=key,
        document_path=str(entry.path) if entry.path else "",
        document_sha256=entry.sha256 or "",
        profile_class=entry.profile_class,
        total_pages=_extract_page_count(manifest),
        ocr_required_pages=int(_decision_field(manifest, "ocr_required_pages") or 0),
        ocr_required_fraction=float(
            _decision_field(manifest, "ocr_required_fraction") or 0.0
        ),
        auto_preferred_backend=(
            _decision_field(manifest, "preferred_backend") if treatment == "auto" else None
        ),
        auto_selected_backend=(
            manifest.get("ocr_backend_selected") if treatment == "auto" else None
        ),
        fallback_reason=_decision_field(manifest, "fallback_reason"),
        exit_status=outcome.exit_status,
        runtime_seconds=outcome.runtime_seconds,
        peak_vram_mib=outcome.peak_vram_mib,
        output_sha256=outcome.output_sha256,
        readiness_score=(
            int(manifest["readiness_score"])
            if manifest.get("readiness_score") is not None
            else None
        ),
        quality_band=manifest.get("quality_band"),
        warning_codes=list(manifest.get("warning_codes") or []),
        output_markdown_length=int(metrics["markdown_length"]),
        output_paragraph_count=int(metrics["paragraphs"]),
        output_heading_count=int(metrics["headings"]),
        output_image_ref_count=int(metrics["image_refs"]),
        output_table_count=int(metrics["tables"]),
        max_repeated_ngram_count=max_repeat,
        repetition_flag=rep_flag,
        source_page_provenance_complete=provenance_ok,
        stderr_tail=outcome.stderr_tail,
        error_message=None if outcome.exit_status == 0 else "compile exited non-zero",
    )


def _missing_document_result(
    entry: CorpusEntry,
    treatment: Treatment,
    key: RunKey,
    *,
    error_message: str = "document not present on disk",
    document_sha256: str | None = None,
) -> RunResult:
    """Return a marker result for a document that isn't on disk.

    ``error_message`` and ``document_sha256`` are overridable so callers
    can distinguish (for example) a skipped local asset (``exit_status=64``
    with ``error_message="skipped_missing_local_asset"`` and an empty
    document_sha256) from a genuinely missing ParseBench asset.
    """
    resolved_sha = document_sha256 if document_sha256 is not None else (entry.sha256 or "")
    return RunResult(
        key=key,
        document_path=str(entry.path) if entry.path else "",
        document_sha256=resolved_sha,
        profile_class=entry.profile_class,
        total_pages=0,
        ocr_required_pages=0,
        ocr_required_fraction=0.0,
        auto_preferred_backend=None,
        auto_selected_backend=None,
        fallback_reason=None,
        exit_status=64,  # conventional "missing input"
        runtime_seconds=0.0,
        peak_vram_mib=None,
        output_sha256=None,
        readiness_score=None,
        quality_band=None,
        warning_codes=[],
        stderr_tail="",
        error_message=error_message,
    )


# ── Acquisition / provenance helpers ──────────────────────────────────


def _safe_sha256_of_file(path: Path | None) -> str:
    """Return the SHA-256 hex of *path* or an empty string on any I/O error.

    Wraps :func:`_sha256_of_file` for the acquisition-metadata path where we
    never want a hashing failure to abort the harness run — a missing or
    unreadable file simply yields an empty hash and lets the acquisition
    envelope record ``resolved=False``.
    """
    if path is None:
        return ""
    try:
        if not path.exists() or not path.is_file():
            return ""
        return _sha256_of_file(path)
    except (OSError, ValueError):
        return ""


def _build_acquisition(entry: CorpusEntry) -> dict[str, Any]:
    """Assemble the per-document acquisition/provenance dict.

    Records the source, on-disk sha256, expected sha256 (where a label or
    lockfile provided one), and — for the ParseBench source — whether the
    on-disk bytes match the lockfile-recorded sha. All fields are safe
    to serialise; missing values are ``""`` (strings) or ``None`` (bools /
    optional refs) so JSON round-trips deterministically.
    """
    source = entry.source
    if source == "parsebench":
        on_disk = _safe_sha256_of_file(entry.path)
        lockfile_sha = entry.sha256 or ""
        parsebench_matches: bool | None
        if on_disk and lockfile_sha:
            parsebench_matches = on_disk == lockfile_sha
        else:
            parsebench_matches = None
        hf_repo_path = entry.extra.get("hf_repo_path") if isinstance(entry.extra, dict) else None
        return {
            "source": "parsebench",
            "asset_id": entry.document_id,
            "expected_path": str(entry.path) if entry.path else "",
            "resolved": entry.resolved,
            "on_disk_sha256": on_disk,
            "lockfile_sha256": lockfile_sha,
            "sha256_matches": parsebench_matches,
            "hf_repo_path": hf_repo_path,
        }

    if source == "synthetic":
        return {
            "source": "synthetic",
            "asset_id": entry.document_id,
            "on_disk_sha256": _safe_sha256_of_file(entry.path),
            "resolved": True,
        }

    if source == "failure":
        return {
            "source": "failure",
            "asset_id": entry.document_id,
            "on_disk_sha256": _safe_sha256_of_file(entry.path),
            "resolved": True,
        }

    if source == "local":
        if not entry.resolved:
            return {
                "source": "local",
                "resolved": False,
                "expected_path": str(entry.path) if entry.path else "",
                "skipped": True,
            }
        on_disk_local = _safe_sha256_of_file(entry.path)
        expected = entry.sha256
        local_matches: bool | None
        if expected:
            local_matches = on_disk_local == expected if on_disk_local else False
        else:
            local_matches = None
        return {
            "source": "local",
            "asset_id": entry.document_id,
            "expected_path": str(entry.path) if entry.path else "",
            "resolved": True,
            "on_disk_sha256": on_disk_local,
            "expected_sha256": expected,
            "sha256_matches": local_matches,
        }

    # Fallback for any unforeseen source label — record source only.
    return {"source": str(source)}


def _load_lockfile_provenance(lockfile: Path) -> tuple[str, str]:
    """Return ``(sha256_of_lockfile_bytes, dataset_revision)`` best-effort.

    On any exception (missing file, malformed JSON) both fields are ``""``.
    """
    if not lockfile.exists() or not lockfile.is_file():
        return "", ""
    try:
        lockfile_sha = _sha256_of_file(lockfile)
    except (OSError, ValueError):
        lockfile_sha = ""
    revision = ""
    try:
        with lockfile.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        source = payload.get("dataset_source") or {}
        revision = str(source.get("dataset_revision") or "")
    except (OSError, ValueError, json.JSONDecodeError):
        revision = ""
    return lockfile_sha, revision


# ── Machine metadata ──────────────────────────────────────────────────


def _machine_metadata() -> dict[str, Any]:
    gpu_name: str | None = None
    vram_total_mib: int | None = None
    if shutil.which("nvidia-smi") is not None:
        try:
            proc = subprocess.run(  # nosec B603 B607 - fixed argv
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                first = proc.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in first.split(",")]
                if len(parts) >= 2:
                    gpu_name = parts[0]
                    vram_total_mib = int(parts[1])
        except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
            pass
    return {
        "gpu_name": gpu_name,
        "vram_total_mib": vram_total_mib,
        "os": f"{platform.system()} {platform.release()}",
        "python_version": sys.version.split()[0],
    }


# ── Public entry point ────────────────────────────────────────────────


def _resolve_document_sha(entry: CorpusEntry) -> str:
    if entry.sha256:
        return entry.sha256
    if entry.path and entry.path.exists():
        return _sha256_of_file(entry.path)
    return ""


def run_harness(
    *,
    entries: list[CorpusEntry] | None = None,
    treatments: tuple[Treatment, ...] = ("tesseract", "unlimited_ocr", "auto"),
    dry_run: bool = False,
    use_cache: bool = True,
    aksharamd_commit: str = "unknown",
    model_revision: str = "unknown",
    cache_dir: Path | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    out_root: Path | None = None,
    lockfile: Path | None = None,
) -> RunReport:
    """Execute the harness and return a :class:`RunReport`.

    :param entries: corpus entries (defaults to :func:`enumerate_corpus`)
    :param treatments: which of the three treatments to run per doc.
    :param dry_run: skip subprocess/GPU entirely; return fabricated numbers.
    :param use_cache: when True, honour cached RunResults.
    :param cache_dir: override the default cache directory.
    :param executor: injectable subprocess.run for tests.
    :param out_root: root directory for compile outputs (dry-run ignores it).
    :param lockfile: path to the ParseBench lockfile used for provenance
        (defaults to :data:`benchmarks.ocr_auto_calibration.corpus._LOCKFILE_DEFAULT`).
    """
    entries = entries if entries is not None else enumerate_corpus()
    started = datetime.now(UTC).isoformat()
    # In dry-run mode we do not touch the system: no nvidia-smi invocation,
    # no subprocess, no GPU. This keeps CI runs deterministic.
    machine = (
        {"gpu_name": None, "vram_total_mib": None,
         "os": f"{platform.system()} {platform.release()}",
         "python_version": sys.version.split()[0]}
        if dry_run
        else _machine_metadata()
    )
    out_root = out_root or (Path(__file__).resolve().parent / ".compile_outputs")
    lockfile_path = lockfile or corpus_mod._LOCKFILE_DEFAULT

    summaries: list[DocumentSummary] = []
    skipped_missing_local_count = 0
    for entry in entries:
        doc_sha = _resolve_document_sha(entry)
        results: dict[Treatment, RunResult] = {}
        # A local optional asset that isn't present on this machine is
        # skipped: we build synthetic marker results (exit_status=64,
        # error_message="skipped_missing_local_asset") for all three
        # treatments and never touch subprocess.
        skip_local = entry.source == "local" and not entry.resolved
        if skip_local:
            skipped_missing_local_count += 1

        for treatment in treatments:
            key = RunKey(
                document_id=entry.document_id,
                treatment=treatment,
                aksharamd_commit=aksharamd_commit,
                model_revision=model_revision,
                harness_schema_version=HARNESS_SCHEMA_VERSION,
            )

            if skip_local:
                result = _missing_document_result(
                    entry,
                    treatment,
                    key,
                    error_message="skipped_missing_local_asset",
                    document_sha256="",
                )
                results[treatment] = result
                continue

            cached = (
                cache_mod.load(doc_sha, key, cache_dir=cache_dir)
                if (use_cache and doc_sha)
                else None
            )
            if cached is not None:
                results[treatment] = cached
                continue

            if dry_run:
                if not entry.path or not entry.path.exists():
                    outcome = _dry_run_outcome(entry.path or Path(entry.document_id), treatment)
                else:
                    outcome = _dry_run_outcome(entry.path, treatment)
                result = _build_run_result(
                    entry=entry, treatment=treatment, key=key, outcome=outcome
                )
            elif not entry.path or not entry.path.exists():
                result = _missing_document_result(entry, treatment, key)
            else:
                out_dir = out_root / entry.document_id / treatment
                outcome = _invoke_compile(
                    entry.path, out_dir, treatment, executor=executor
                )
                result = _build_run_result(
                    entry=entry, treatment=treatment, key=key, outcome=outcome
                )

            if use_cache and doc_sha:
                cache_mod.store(doc_sha, result, cache_dir=cache_dir)
            results[treatment] = result

        summary = build_document_summary(
            document_id=entry.document_id,
            profile_class=entry.profile_class,
            tesseract=results["tesseract"],
            unlimited_ocr=results["unlimited_ocr"],
            auto=results["auto"],
        )
        summary.acquisition = _build_acquisition(entry)
        summaries.append(summary)

    # Corpus-level provenance envelope: lockfile checksum + revision so a
    # reviewer can verify which snapshot the calibration was run against,
    # plus per-source counts and skipped-local count.
    lockfile_sha, dataset_revision = _load_lockfile_provenance(lockfile_path)
    counts_by_source: dict[str, int] = {
        "parsebench": 0, "synthetic": 0, "failure": 0, "local": 0,
    }
    resolved_counts_by_source: dict[str, int] = {
        "parsebench": 0, "synthetic": 0, "failure": 0, "local": 0,
    }
    for entry in entries:
        counts_by_source[entry.source] = counts_by_source.get(entry.source, 0) + 1
        if entry.resolved:
            resolved_counts_by_source[entry.source] = (
                resolved_counts_by_source.get(entry.source, 0) + 1
            )
    corpus_provenance: dict[str, Any] = {
        "parsebench_lockfile_path": str(lockfile_path),
        "parsebench_lockfile_sha256": lockfile_sha,
        "parsebench_dataset_revision": dataset_revision,
        "counts_by_source": counts_by_source,
        "resolved_counts_by_source": resolved_counts_by_source,
        "skipped_missing_local_count": skipped_missing_local_count,
    }

    completed = datetime.now(UTC).isoformat()
    return RunReport(
        harness_schema_version=HARNESS_SCHEMA_VERSION,
        aksharamd_commit=aksharamd_commit,
        model_revision=model_revision,
        run_started_at=started,
        run_completed_at=completed,
        machine=machine,
        corpus_size=len(entries),
        documents=summaries,
        corpus_provenance=corpus_provenance,
    )
