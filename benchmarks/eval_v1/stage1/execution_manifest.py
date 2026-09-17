"""Stage 1 Execution Manifest writer (B1a-9).

Writes docs/evaluation/STAGE1_EXECUTION_MANIFEST.json before the first
parser or Track C LLM call.  The manifest binds the execution to the
authorized pre-execution state and serves as the provenance anchor for
every execution record produced during Stage 1.

Fail-closed contract
--------------------
The writer refuses to produce a manifest if any of the following diverge
from the pinned values recorded at study-freeze close (3a43313):

  * STUDY_FREEZE_MANIFEST_V1.md canonical-LF SHA-256
  * STAGE1_CORPUS_SNAPSHOT_MANIFEST_V1.md canonical-LF SHA-256
  * TRACK_C_PROMPT_V1.txt canonical-LF SHA-256
  * STAGE1_DOCLAYNET_VAL_SELECTION.json raw-byte SHA-256
  * STAGE1_FINTABNET_C_SELECTION.json raw-byte SHA-256
  * tmp/olmocr-full-data/acquisition.json raw-byte SHA-256
  * SCORING_POLICY_VERSION in aksharamd/scoring/models.py
  * installed package versions for all four parsers

Any failure is printed with a clear label and the script exits non-zero
without writing any manifest file.

Run
---
    python -m benchmarks.eval_v1.stage1.execution_manifest

Output
------
    docs/evaluation/STAGE1_EXECUTION_MANIFEST.json
"""
from __future__ import annotations

import hashlib
import importlib.metadata as im
import json
import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.smoke_b1a_7b.provenance import compute_model_artifact_sha256

ROOT = Path(__file__).parent.parent.parent.parent

# ---------------------------------------------------------------------------
# Pinned integrity constants (locked at 3a43313 / manifest 8858b8e9…)
# ---------------------------------------------------------------------------

AUTHORIZED_DEVELOP_COMMIT = "3a4331303d45b940f1706b0861806f3c414160ac"

# Canonical-LF SHA-256 (content normalized to LF before hashing)
FROZEN_STUDY_FREEZE_SHA = (
    "8858b8e9fa5d4397e688a2253d60217d38b20b34e84bce02e8493bbbc60ad02a"
)
FROZEN_CORPUS_SNAPSHOT_SHA = (
    "0b026cbd52e45dde2e956e390458f3246452d943090154c5089fc37237bfe7de"
)
FROZEN_TRACK_C_PROMPT_SHA = (
    "2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601"
)

# Raw-byte SHA-256 (JSON / binary; LF normalization not applied)
FROZEN_DOCLAYNET_SELECTION_SHA = (
    "efbe3729121117ad5d03e715605bc84eef295ca701d5ff238b33a175981b2df2"
)
FROZEN_FINTABNET_C_SELECTION_SHA = (
    "f8ec903aecdae4a0c49484d42539b220d941d19ce79e5489f62796c02062427e"
)
FROZEN_OLMOCR_ACQUISITION_SHA = (
    "ca9aa04e6b91ad00a2cf9c0e49e0512de7adb5e2c3b45a0bbd1adf2d629cfe58"
)

# Parser execution contract — canonical-LF SHA-256
FROZEN_CONTRACT_SHA = (
    "a0ca496e562cee200c393c146c7ed16efee9b01ee2f94ebdd4e623c936f9baa4"
)

FROZEN_SCORING_POLICY_VERSION = "1.10"
FREEZE_SEED = (
    "6c270ac293b348ca27279bdd012375aa6c707be494085e70781637a99a6322fa"
)

# VLM model artifact SHA-256 (computed by compute_model_artifact_sha256).
# marker uses the Surya model cache; docling SHA is the combined hash of
# docling-models and docling-layout-heron (sha256(sha_models + ":" + sha_heron)).
FROZEN_MARKER_MODEL_SHA = (
    "176167fd0f8b2b224a5d7766668d705e788f855469056270bb8009c7499c87b8"
)
FROZEN_DOCLING_MODEL_SHA = (
    "cb8a5fc6b19866420fb8839efddda07bf61dd21713135c1f480bbde056592fc4"
)

FROZEN_PACKAGES: dict[str, str] = {
    "aksharamd": "0.3.6",
    "marker-pdf": "1.10.2",
    "docling": "2.107.0",
    "markitdown": "0.1.6",
    "pymupdf": "1.28.2",
    "torch": "2.12.1+cu126",
}

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

STUDY_FREEZE_PATH = ROOT / "docs" / "evaluation" / "STUDY_FREEZE_MANIFEST_V1.md"
CORPUS_SNAPSHOT_PATH = (
    ROOT / "docs" / "evaluation" / "STAGE1_CORPUS_SNAPSHOT_MANIFEST_V1.md"
)
TRACK_C_PROMPT_PATH = ROOT / "docs" / "evaluation" / "TRACK_C_PROMPT_V1.txt"
DOCLAYNET_SELECTION_PATH = (
    ROOT / "docs" / "evaluation" / "STAGE1_DOCLAYNET_VAL_SELECTION.json"
)
FINTABNET_C_SELECTION_PATH = (
    ROOT / "docs" / "evaluation" / "STAGE1_FINTABNET_C_SELECTION.json"
)
OLMOCR_ACQUISITION_PATH = (
    ROOT / "tmp" / "olmocr-full-data" / "acquisition.json"
)
CONTRACT_PATH = (
    ROOT / "benchmarks" / "eval_v1" / "config" / "parser_execution_contract_v1.json"
)
MODELS_PY_PATH = ROOT / "aksharamd" / "scoring" / "models.py"

OUTPUT_PATH = ROOT / "docs" / "evaluation" / "STAGE1_EXECUTION_MANIFEST.json"

# VLM model cache directories (override via env vars if needed).
# marker uses the Surya datalab cache; docling uses two HF hub subdirs.
MARKER_MODEL_CACHE_PATH: Path = Path(
    re.sub(r"^$", "", os.environ.get("MARKER_ARTIFACTS_CACHE", ""))
    or str(Path.home() / "AppData" / "Local" / "datalab" / "datalab" / "Cache" / "models")
)
DOCLING_MODELS_PATH: Path = Path(
    re.sub(r"^$", "", os.environ.get("DOCLING_MODELS_PATH", ""))
    or str(Path.home() / ".cache" / "huggingface" / "hub" / "models--docling-project--docling-models")
)
DOCLING_HERON_PATH: Path = Path(
    re.sub(r"^$", "", os.environ.get("DOCLING_HERON_PATH", ""))
    or str(Path.home() / ".cache" / "huggingface" / "hub" / "models--docling-project--docling-layout-heron")
)

# Run-root definitions (pre-declared before any execution)
RUN_ROOTS: dict[str, str] = {
    "track_a_olmocr": "benchmarks/results/stage1-track-a-olmocr-2026-09-16",
    "track_a_doclaynet": "benchmarks/results/stage1-track-a-doclaynet-2026-09-16",
    "track_c": "benchmarks/results/stage1-track-c-2026-09-16",
}


class ExecutionManifestIntegrityError(RuntimeError):
    """One or more pre-execution integrity checks failed."""


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _lf_sha256(path: Path) -> str:
    raw = path.read_bytes()
    return hashlib.sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Integrity gate
# ---------------------------------------------------------------------------

def _verify_all() -> tuple[dict[str, str], list[str]]:
    """Run all integrity checks.  Returns (observed_values, failures).

    observed_values maps label → actual SHA / version (for manifest embedding).
    failures is empty on success.
    """
    failures: list[str] = []
    observed: dict[str, str] = {}

    def _check(label: str, path: Path | None, expected: str, actual: str) -> None:
        observed[label] = actual
        if actual != expected:
            loc = f" ({path})" if path else ""
            failures.append(
                f"{label}{loc}\n"
                f"    expected : {expected}\n"
                f"    actual   : {actual}"
            )

    # 1. Study freeze manifest (canonical LF)
    if not STUDY_FREEZE_PATH.exists():
        failures.append(f"study freeze manifest not found: {STUDY_FREEZE_PATH}")
    else:
        _check(
            "study_freeze_manifest_sha256 (canonical-LF)",
            STUDY_FREEZE_PATH,
            FROZEN_STUDY_FREEZE_SHA,
            _lf_sha256(STUDY_FREEZE_PATH),
        )

    # 2. Corpus snapshot manifest (canonical LF)
    if not CORPUS_SNAPSHOT_PATH.exists():
        failures.append(f"corpus snapshot manifest not found: {CORPUS_SNAPSHOT_PATH}")
    else:
        _check(
            "corpus_snapshot_manifest_sha256 (canonical-LF)",
            CORPUS_SNAPSHOT_PATH,
            FROZEN_CORPUS_SNAPSHOT_SHA,
            _lf_sha256(CORPUS_SNAPSHOT_PATH),
        )

    # 3. Track C prompt (canonical LF — frozen by B1a-8d / §15)
    if not TRACK_C_PROMPT_PATH.exists():
        failures.append(f"Track C prompt not found: {TRACK_C_PROMPT_PATH}")
    else:
        _check(
            "track_c_prompt_sha256 (canonical-LF)",
            TRACK_C_PROMPT_PATH,
            FROZEN_TRACK_C_PROMPT_SHA,
            _lf_sha256(TRACK_C_PROMPT_PATH),
        )

    # 4. DocLayNet val selection (raw bytes — JSON is deterministic)
    if not DOCLAYNET_SELECTION_PATH.exists():
        failures.append(f"DocLayNet selection not found: {DOCLAYNET_SELECTION_PATH}")
    else:
        _check(
            "doclaynet_val_selection_sha256 (raw-byte)",
            DOCLAYNET_SELECTION_PATH,
            FROZEN_DOCLAYNET_SELECTION_SHA,
            _raw_sha256(DOCLAYNET_SELECTION_PATH),
        )

    # 5. FinTabNet.c selection (raw bytes — retained as provenance, NOT_EXECUTABLE_V1)
    if not FINTABNET_C_SELECTION_PATH.exists():
        failures.append(
            f"FinTabNet.c selection not found: {FINTABNET_C_SELECTION_PATH}"
        )
    else:
        _check(
            "fintabnet_c_selection_sha256 (raw-byte)",
            FINTABNET_C_SELECTION_PATH,
            FROZEN_FINTABNET_C_SELECTION_SHA,
            _raw_sha256(FINTABNET_C_SELECTION_PATH),
        )

    # 6. olmOCR acquisition receipt (raw bytes)
    if not OLMOCR_ACQUISITION_PATH.exists():
        failures.append(
            f"olmOCR acquisition receipt not found: {OLMOCR_ACQUISITION_PATH}"
        )
    else:
        _check(
            "olmocr_acquisition_receipt_sha256 (raw-byte)",
            OLMOCR_ACQUISITION_PATH,
            FROZEN_OLMOCR_ACQUISITION_SHA,
            _raw_sha256(OLMOCR_ACQUISITION_PATH),
        )

    # 7. Parser execution contract (canonical LF)
    if not CONTRACT_PATH.exists():
        failures.append(f"parser execution contract not found: {CONTRACT_PATH}")
    else:
        _check(
            "parser_execution_contract_sha256 (canonical-LF)",
            CONTRACT_PATH,
            FROZEN_CONTRACT_SHA,
            _lf_sha256(CONTRACT_PATH),
        )

    # 8. SCORING_POLICY_VERSION in codebase
    if not MODELS_PY_PATH.exists():
        failures.append(f"aksharamd/scoring/models.py not found: {MODELS_PY_PATH}")
    else:
        text = MODELS_PY_PATH.read_text(encoding="utf-8")
        m = re.search(
            r'^SCORING_POLICY_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE
        )
        if not m:
            failures.append("SCORING_POLICY_VERSION not found in models.py")
        else:
            actual_spv = m.group(1)
            observed["scoring_policy_version"] = actual_spv
            if actual_spv != FROZEN_SCORING_POLICY_VERSION:
                failures.append(
                    f"SCORING_POLICY_VERSION\n"
                    f"    expected : {FROZEN_SCORING_POLICY_VERSION!r}\n"
                    f"    actual   : {actual_spv!r}"
                )

    # 9. Installed package versions
    for pkg, pinned in FROZEN_PACKAGES.items():
        try:
            actual_ver = im.version(pkg)
        except im.PackageNotFoundError:
            failures.append(f"package {pkg!r} not installed")
            observed[f"package_{pkg}"] = "NOT_INSTALLED"
            continue
        observed[f"package_{pkg}"] = actual_ver
        if actual_ver != pinned:
            failures.append(
                f"package {pkg!r}\n"
                f"    expected : {pinned}\n"
                f"    actual   : {actual_ver}"
            )

    # 10. Marker (Surya) model artifact SHA-256
    if not MARKER_MODEL_CACHE_PATH.exists():
        failures.append(
            f"marker model cache not found: {MARKER_MODEL_CACHE_PATH}\n"
            f"    Set MARKER_ARTIFACTS_CACHE env var to the Surya cache dir."
        )
    else:
        try:
            actual_marker_sha = compute_model_artifact_sha256(MARKER_MODEL_CACHE_PATH)
            observed["marker_model_artifact_sha256"] = actual_marker_sha
            if actual_marker_sha != FROZEN_MARKER_MODEL_SHA:
                failures.append(
                    f"marker model artifact SHA-256\n"
                    f"    expected : {FROZEN_MARKER_MODEL_SHA}\n"
                    f"    actual   : {actual_marker_sha}\n"
                    f"    cache    : {MARKER_MODEL_CACHE_PATH}"
                )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"marker model artifact hash error: {exc}")

    # 11. Docling model artifact SHA-256 (combined: docling-models + docling-heron)
    docling_missing = [
        p for p in (DOCLING_MODELS_PATH, DOCLING_HERON_PATH) if not p.exists()
    ]
    if docling_missing:
        failures.append(
            "docling model cache dir(s) not found: "
            + ", ".join(str(p) for p in docling_missing)
        )
    else:
        try:
            sha_dm = compute_model_artifact_sha256(DOCLING_MODELS_PATH)
            sha_dh = compute_model_artifact_sha256(DOCLING_HERON_PATH)
            actual_docling_sha = hashlib.sha256(
                f"{sha_dm}:{sha_dh}".encode()
            ).hexdigest()
            observed["docling_model_artifact_sha256"] = actual_docling_sha
            observed["docling_models_sha256"] = sha_dm
            observed["docling_heron_sha256"] = sha_dh
            if actual_docling_sha != FROZEN_DOCLING_MODEL_SHA:
                failures.append(
                    f"docling model artifact SHA-256\n"
                    f"    expected : {FROZEN_DOCLING_MODEL_SHA}\n"
                    f"    actual   : {actual_docling_sha}\n"
                    f"    models   : {sha_dm}\n"
                    f"    heron    : {sha_dh}"
                )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"docling model artifact hash error: {exc}")

    return observed, failures


# ---------------------------------------------------------------------------
# Environment capture
# ---------------------------------------------------------------------------

def _capture_env() -> dict[str, Any]:
    git_commit = "unknown"
    try:
        git_commit = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5
            )
            .decode("ascii")
            .strip()
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    cuda_version: str | None = None
    cuda_device: str | None = None
    try:
        import torch
        if torch.cuda.is_available():
            cuda_version = torch.version.cuda
            cuda_device = torch.cuda.get_device_name(0)
    except ImportError:
        pass

    cpu_cores: int | None = None
    try:
        import psutil
        cpu_cores = psutil.cpu_count(logical=False)
    except ImportError:
        pass

    return {
        "git_commit": git_commit,
        "python_version": sys.version.split()[0],
        "platform_string": platform.platform(),
        "cpu_physical_cores": cpu_cores,
        "cuda_version": cuda_version,
        "cuda_device_name": cuda_device,
    }


# ---------------------------------------------------------------------------
# Manifest assembly and writer
# ---------------------------------------------------------------------------

def run(*, output_path: Path = OUTPUT_PATH, verbose: bool = True) -> dict[str, Any]:
    """Run integrity checks then write the manifest.  Returns the manifest dict.

    Raises ExecutionManifestIntegrityError if any check fails.
    """
    if verbose:
        print("=== STAGE 1 EXECUTION MANIFEST — integrity gate ===")

    observed, failures = _verify_all()

    if failures:
        print()
        print(f"INTEGRITY GATE FAILED — {len(failures)} violation(s):")
        for f in failures:
            print(f"  FAIL: {f}")
        print()
        print("Manifest not written.  Resolve all failures before Stage 1 execution.")
        raise ExecutionManifestIntegrityError(
            f"{len(failures)} integrity check(s) failed; manifest not written"
        )

    if verbose:
        print("  All integrity checks passed.")

    env = _capture_env()

    manifest: dict[str, Any] = {
        "schema_version": "1",
        "phase": "stage1",
        "written_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "study_freeze_manifest": {
            "path": str(STUDY_FREEZE_PATH.relative_to(ROOT)),
            "canonical_lf_sha256": FROZEN_STUDY_FREEZE_SHA,
            "authorized_develop_commit": AUTHORIZED_DEVELOP_COMMIT,
        },
        "corpus_snapshot_manifest": {
            "path": str(CORPUS_SNAPSHOT_PATH.relative_to(ROOT)),
            "canonical_lf_sha256": FROZEN_CORPUS_SNAPSHOT_SHA,
        },
        "freeze_seed": FREEZE_SEED,
        "scoring_policy_version": FROZEN_SCORING_POLICY_VERSION,
        "parser_execution_contract": {
            "version": "v1",
            "path": str(CONTRACT_PATH.relative_to(ROOT)),
            "canonical_lf_sha256": FROZEN_CONTRACT_SHA,
        },
        "executable_track_a": ["olmocr_bench", "doclaynet_val"],
        "corpora": {
            "olmocr_bench": {
                "hf_dataset_id": "allenai/olmOCR-bench",
                "hf_revision": "54a96a6fb6a2bd3b297e59869491db4d3625b711",
                "license": "ODC-BY",
                "n_pdfs": 1403,
                "n_assertions": 7010,
                "local_path": "tmp/olmocr-full-data",
                "acquisition_receipt": {
                    "path": str(OLMOCR_ACQUISITION_PATH.relative_to(ROOT)),
                    "raw_byte_sha256": FROZEN_OLMOCR_ACQUISITION_SHA,
                },
                "execution_status": "EXECUTABLE",
            },
            "doclaynet_val": {
                "hf_dataset_id": "docling-project/DocLayNet-v1.2",
                "hf_revision": "0daf93102e2efce76c3e11a274a5e0d0969391d3",
                "license": "CDLA-Permissive",
                "split": "validation",
                "n_selected": 280,
                "selection_manifest": {
                    "path": str(DOCLAYNET_SELECTION_PATH.relative_to(ROOT)),
                    "raw_byte_sha256": FROZEN_DOCLAYNET_SELECTION_SHA,
                },
                "execution_status": "EXECUTABLE",
            },
            "fintabnet_c": {
                "hf_dataset_id": "bsmock/FinTabNet.c",
                "hf_revision": "e5673a90b98d02c4832f9e836d72762f0e8933a0",
                "license": "CDLA-Permissive-2.0",
                "n_selected": 500,
                "selection_manifest": {
                    "path": str(FINTABNET_C_SELECTION_PATH.relative_to(ROOT)),
                    "raw_byte_sha256": FROZEN_FINTABNET_C_SELECTION_SHA,
                },
                "execution_status": "NOT_EXECUTABLE_V1",
                "not_executable_reason": (
                    "Source PDFs unavailable from authoritative distribution; "
                    "TEDS endpoint = NOT_MEASURED_V1. "
                    "See STAGE1_CORPUS_SNAPSHOT_MANIFEST_V1.md §3 and "
                    "STUDY_FREEZE_MANIFEST_V1.md §6.3."
                ),
            },
            "omni_doc_bench": {
                "hf_dataset_id": "lllcho/OmniDocBench",
                "hf_revision": "91fe284bbfacfa687959ae3eb00846ca852aa907",
                "execution_status": "EXCLUDED",
                "excluded_reason": (
                    "Image-only corpus (JPEGs + PNGs); no PDFs; "
                    "all four frozen parsers require PDF input. "
                    "See STUDY_FREEZE_MANIFEST_V1.md §6.4."
                ),
            },
        },
        "parsers": [
            {
                "parser_id": "aksharamd-reference",
                "package": "aksharamd",
                "package_version": FROZEN_PACKAGES["aksharamd"],
                "hardware": "cpu_only",
                "timeout_seconds": 120,
            },
            {
                "parser_id": "marker",
                "package": "marker-pdf",
                "package_version": FROZEN_PACKAGES["marker-pdf"],
                "hardware": "gpu",
                "timeout_seconds": 600,
            },
            {
                "parser_id": "docling",
                "package": "docling",
                "package_version": FROZEN_PACKAGES["docling"],
                "hardware": "gpu",
                "timeout_seconds": 600,
            },
            {
                "parser_id": "markitdown",
                "package": "markitdown",
                "package_version": FROZEN_PACKAGES["markitdown"],
                "hardware": "cpu_only",
                "timeout_seconds": 120,
            },
        ],
        "track_c": {
            "model_id": "claude-sonnet-4-6",
            "temperature": 0.0,
            "competitor_arm": "none",
            "prompt": {
                "path": str(TRACK_C_PROMPT_PATH.relative_to(ROOT)),
                "canonical_lf_sha256": FROZEN_TRACK_C_PROMPT_SHA,
            },
            "corpora": {
                "qasper": {"n_target": 50},
                "mmlong_bench_doc": {"n_target": 30},
                "tat_dqa": {"n_target": 30},
            },
        },
        "run_roots": RUN_ROOTS,
        "model_cache_paths": {
            "marker": str(MARKER_MODEL_CACHE_PATH),
            "docling_models": str(DOCLING_MODELS_PATH),
            "docling_heron": str(DOCLING_HERON_PATH),
        },
        "model_artifact_shas": {
            "marker": observed.get("marker_model_artifact_sha256", ""),
            "docling": observed.get("docling_model_artifact_sha256", ""),
            "docling_models_component": observed.get("docling_models_sha256", ""),
            "docling_heron_component": observed.get("docling_heron_sha256", ""),
        },
        "environment": env,
        "admission_batch_status": "PENDING",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(manifest, indent=2, sort_keys=True)
    output_path.write_text(body, encoding="utf-8")

    manifest_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()

    if verbose:
        print()
        print("=== STAGE 1 EXECUTION MANIFEST WRITTEN ===")
        print(f"  path          : {output_path}")
        print(f"  written_utc   : {manifest['written_utc']}")
        print(f"  git_commit    : {env['git_commit']}")
        print(f"  manifest SHA  : {manifest_sha}")
        print()
        print("  VLM model artifact SHAs:")
        print(f"    marker  : {manifest['model_artifact_shas']['marker'][:24]}…")
        print(f"    docling : {manifest['model_artifact_shas']['docling'][:24]}…")
        print()
        print("  Corpus summary:")
        print("    olmOCR-Bench  : EXECUTABLE (1,403 PDFs)")
        print("    DocLayNet val : EXECUTABLE (280 pages)")
        print("    FinTabNet.c   : NOT_EXECUTABLE_V1 (TEDS = NOT_MEASURED_V1)")
        print("    OmniDocBench  : EXCLUDED")
        print()
        print("  Record this manifest SHA before starting the runner:")
        print(f"    {manifest_sha}")

    return manifest


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=OUTPUT_PATH)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    try:
        run(output_path=args.output, verbose=not args.quiet)
    except ExecutionManifestIntegrityError as e:
        print(f"\nAborted: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
