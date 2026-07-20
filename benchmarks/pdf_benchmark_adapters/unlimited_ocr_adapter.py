"""Baidu Unlimited-OCR adapter for PDF Benchmark v1 Phase 2 (Issue #68).

Runs Baidu's Unlimited-OCR vision-language model on the same 45 eligible
assets that AksharaMD Phase 1, PyMuPDF4LLM, and MarkItDown consumed.
Reports **tool-neutral** metrics only. No cross-parser ranking is
produced here — Phase 3 will combine adapters after each is
independently reviewed.

**No AksharaMD production code changes.** No parser, validator,
scoring, warning-penalty, or ``SCORING_POLICY`` modifications.
``SCORING_POLICY_VERSION`` remains ``"1.0"``.

## Local-only inference model

Unlimited-OCR is a **local vision-language OCR model** (Baidu, released
2026-06-22, HuggingFace repo ``baidu/Unlimited-OCR``, arXiv:2606.23050).
Runs on-device on an NVIDIA GPU with BF16 support.

- **Model repo:** ``baidu/Unlimited-OCR`` on HuggingFace.
- **Pinned revision:** see ``_UNLIMITED_OCR_MODEL_REVISION`` below. This
  MUST match a specific commit hash — not a mutable branch — because
  the load path uses ``trust_remote_code=True``.
- **Model download:** NOT performed by this adapter. The user or a
  companion command (``aksharamd models install unlimited-ocr``,
  designed in the ADR) must trigger it explicitly. During benchmark
  execution the adapter fails cleanly with a distinct error if the
  model is not already in the local HuggingFace cache.
- **Weight format:** ``use_safetensors=True`` — refuses pickle-based
  weights.
- **dtype:** ``torch.bfloat16``.
- **Inference API:** ``model.infer_multi()`` for multi-page PDFs with
  bounded batch size; the adapter renders PDF pages to 300-DPI PNGs
  via PyMuPDF into a temp dir that is cleaned up after each call.

## Offline enforcement

The adapter sets ``HF_HUB_OFFLINE=1`` and ``TRANSFORMERS_OFFLINE=1``
BEFORE importing ``transformers`` in the child process. This forbids
HuggingFace Hub network access during inference. If the model is not
already cached locally, the load fails with ``OFFLINE_MODEL_MISSING``
and the adapter records that verbatim per asset. No network telemetry
is sent — the model is loaded from the local cache and inference is
GPU-local.

**Security note.** ``trust_remote_code=True`` is required by the
official loading path (custom preprocessor + model code live in the
model repo). This adapter:

- Pins the exact model revision (below) — no mutable branch.
- Records the ``model.config._name_or_path`` and revision in every
  output artifact for reviewer audit.
- Does NOT execute any code from the model repo at import time —
  only inside the child process, after the model is loaded.

## Evaluation semantics — same as other Phase-2 adapters

Uses the SAME tool-neutral metric definitions as
``pymupdf4llm_adapter.py`` and ``markitdown_adapter.py``. No AksharaMD
readiness score, quality band, or warning codes.

## Execution modes

The adapter supports three runtime modes:

- ``--dry-run`` (default when weights missing) — mocks the inference
  call, records "not_run" per asset, still emits a full JSON + MD
  report with the deterministic manifest identity so tests can verify
  adapter shape without requiring the model.
- ``--real`` — attempts real inference; fails cleanly per asset if the
  model isn't in the local cache. Requires an NVIDIA GPU with BF16
  support.
- Automatic mode: real inference if the model is cached AND CUDA is
  available; dry-run otherwise. The mode used is recorded in the
  aggregate output.

Refuses to run if the pinned Python packages are missing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"

# ── Pinned model reference ──────────────────────────────────────────────

# Pinned model reference. Approved by reviewer 2026-07-20 after A0
# download + static security review at this revision. See:
#   docs/adr/ocr_backend_execution_plan.md  (Phase A0 / A1a)
#   docs/security/unlimited_ocr_static_review_d549bb9d.md
_UNLIMITED_OCR_MODEL_REPO = "baidu/Unlimited-OCR"
_UNLIMITED_OCR_MODEL_REVISION: str | None = "d549bb9d6a055dbe291408916d66acc2cd5920f6"

# Trusted-code manifest is now a committed JSON file — the single
# source of truth for what belongs in the model snapshot at the
# pinned revision. See:
#   aksharamd/plugins/ocr_backends/unlimited_ocr_trusted_manifest.json
_UNLIMITED_OCR_TRUSTED_MANIFEST_SUPPORTED_SCHEMA = 1

# Legacy inline dict — retained ONLY for the low-level
# ``verify_trusted_code_files`` primitive which pre-existing unit tests
# call directly with an in-memory hash table. Production callers use
# ``verify_snapshot_against_manifest`` with the JSON manifest.
_UNLIMITED_OCR_TRUSTED_CODE_FILES: dict[str, str] = {}


# ── Metrics (identical to the other adapters for cross-tool parity) ──


_MIN_MEANINGFUL_CHARS = 200
_IMG_PLACEHOLDER_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_USABLE_ENUM = {"usable", "usable_with_minor_defects"}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _repeat_content_ratio(text: str, ngram: int = 4) -> float:
    tokens = text.split()
    if len(tokens) < ngram * 2:
        return 0.0
    counts: dict[tuple[str, ...], int] = {}
    for i in range(len(tokens) - ngram + 1):
        key = tuple(tokens[i:i + ngram])
        counts[key] = counts.get(key, 0) + 1
    dup_windows = sum(c for c in counts.values() if c > 1)
    total = len(tokens) - ngram + 1
    return dup_windows / total if total else 0.0


def _image_placeholder_ratio(text: str) -> float | None:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    imgs = sum(1 for ln in lines if _IMG_PLACEHOLDER_RE.search(ln))
    return round(imgs / len(lines), 4)


def _hidden_text_layer_chars(p: Path) -> tuple[bool | None, int | None]:
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError:
        return None, None
    try:
        with fitz.open(str(p)) as doc:
            total = sum(len(page.get_text() or "") for page in doc)
            return (total > 0, total)
    except Exception:
        return None, None


# ── Result records ───────────────────────────────────────────────────────


@dataclass
class RunResult:
    asset_id: str
    corpus_source: str
    document_class: str
    execution_success: bool
    execution_mode: str  # "real_inference" | "dry_run" | "model_not_cached" | "no_gpu" | "deps_missing"
    exception: str
    output_package_created: bool
    content_extracted: bool
    structurally_usable: bool
    human_review_status: str
    human_usability: str
    human_review_evidence: str
    runtime_seconds: float
    output_chars: int
    non_whitespace_chars: int
    estimated_tokens: int
    output_size_inflation: float
    deterministic: bool | None
    page_count_pdf: int | None
    hidden_text_layer: bool | None
    hidden_text_layer_chars: int | None
    image_placeholder_ratio: float | None
    repeat_content_ratio: float | None = None
    near_empty_equivalent: bool = False
    low_density_equivalent: bool = False
    peak_gpu_memory_mib: int | None = None
    tool_signals: dict[str, Any] = field(default_factory=dict)


# ── Environment inspection ──────────────────────────────────────────────


def _pinned_deps_present() -> tuple[bool, str]:
    missing = []
    for pkg in ("torch", "transformers", "pymupdf", "einops", "addict", "easydict", "PIL"):
        try:
            __import__(pkg if pkg != "PIL" else "PIL.Image")
        except ImportError:
            missing.append(pkg)
    if missing:
        return False, f"missing packages: {missing}"
    return True, ""


def _gpu_capability_report() -> dict[str, Any]:
    try:
        import torch  # type: ignore
    except ImportError:
        return {"torch_installed": False, "cuda_available": False}
    out: dict[str, Any] = {
        "torch_installed": True,
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
    }
    if not out["cuda_available"]:
        return out
    out["cuda_version"] = torch.version.cuda
    out["device_count"] = torch.cuda.device_count()
    if torch.cuda.device_count() > 0:
        props = torch.cuda.get_device_properties(0)
        out["device_0_name"] = props.name
        out["device_0_vram_gib"] = round(props.total_memory / 1024**3, 2)
        out["device_0_compute_capability"] = f"{props.major}.{props.minor}"
        out["bf16_supported"] = torch.cuda.is_bf16_supported()
    return out


def _model_cached_locally(repo: str, revision: str | None) -> tuple[bool, str]:
    """Check the HuggingFace hub cache for the pinned model. Does NOT
    perform any network call.
    """
    try:
        from huggingface_hub import snapshot_download  # noqa: F401  # type: ignore[import-untyped]
    except ImportError:
        return False, "huggingface_hub not installed"
    if not revision:
        return False, "no pinned revision configured (see _UNLIMITED_OCR_MODEL_REVISION)"
    # Cache path convention: HF_HOME defaults to ~/.cache/huggingface/hub
    cache_root = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface" / "hub")
    repo_dir = cache_root / f"models--{repo.replace('/', '--')}"
    if not repo_dir.exists():
        return False, f"model repo cache directory missing: {repo_dir}"
    snapshots = repo_dir / "snapshots"
    if not snapshots.exists():
        return False, "no snapshots directory in model cache"
    # Look for a directory whose name matches the pinned revision
    candidates = [p for p in snapshots.iterdir() if p.is_dir()]
    if not candidates:
        return False, "no snapshot directories present"
    for c in candidates:
        if c.name.startswith(revision[:12]) or c.name == revision:
            return True, str(c)
    return False, f"pinned revision {revision[:12]}... not present in {[c.name for c in candidates]}"


# ── Inference ──────────────────────────────────────────────────────────


def _pdf_to_page_images(pdf: Path, out_dir: Path, dpi: int = 300) -> list[Path]:
    """Render each PDF page to a PNG under ``out_dir``. Deterministic
    filenames: ``page_{n:04d}.png``.
    """
    import fitz  # type: ignore[import-untyped]
    paths: list[Path] = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    with fitz.open(str(pdf)) as doc:
        for i, page in enumerate(doc, start=1):
            p = out_dir / f"page_{i:04d}.png"
            page.get_pixmap(matrix=mat).save(str(p))
            paths.append(p)
    return paths


def verify_trusted_code_files(
    repo: str,
    revision: str | None,
    trusted: dict[str, str],
) -> tuple[bool, str]:
    """Fail-closed verification of the custom Python files inside the
    pinned model snapshot.

    Because ``trust_remote_code=True`` is required by Unlimited-OCR's
    official loading path, EVERY ``.py`` file in the model repo that
    will be executed at load time must be pinned to a known SHA-256.

    Refusal conditions (any one → refuse):

    - ``revision`` is ``None`` (would allow a mutable branch reference)
    - ``trusted`` is empty (no hash table to compare against)
    - the pinned snapshot directory is missing from the local cache
    - any file listed in ``trusted`` is missing from the snapshot
    - any file's actual SHA-256 differs from the pinned value
    - an EXTRA ``.py`` file appears in the snapshot that is not in
      the trusted table (would mean an unreviewed file could execute)

    Returns ``(ok, note)``. When ``ok`` is False, ``note`` explains
    exactly which condition failed. Never raises.
    """
    if revision is None:
        return False, "revision unset — mutable branch references are refused"
    if not trusted:
        return False, (
            "trusted-code hash table is empty; refuse to load remote code "
            "without an approved SHA-256 for every custom .py file"
        )
    try:
        from huggingface_hub import snapshot_download  # noqa: F401  # type: ignore[import-untyped]
    except ImportError:
        return False, "huggingface_hub not installed; cannot locate model snapshot"
    cache_root = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface" / "hub")
    repo_dir = cache_root / f"models--{repo.replace('/', '--')}"
    snapshots = repo_dir / "snapshots"
    if not snapshots.exists():
        return False, f"no snapshots directory in model cache: {snapshots}"
    snap: Path | None = None
    for c in snapshots.iterdir():
        if c.is_dir() and (c.name == revision or c.name.startswith(revision[:12])):
            snap = c
            break
    if snap is None:
        return False, f"pinned revision {revision[:12]}... not present in local snapshots"
    # Every listed file must exist and match.
    for rel, expected_sha in trusted.items():
        p = snap / rel
        if not p.exists():
            return False, f"trusted file missing from snapshot: {rel}"
        actual = sha256_file(p)
        if actual != expected_sha:
            return False, (
                f"SHA-256 mismatch on {rel}: expected {expected_sha[:12]}..., got {actual[:12]}..."
            )
    # No EXTRA .py files may appear that aren't in the trusted table.
    trusted_set = set(trusted)
    for py in snap.rglob("*.py"):
        rel = str(py.relative_to(snap)).replace("\\", "/")
        if rel not in trusted_set:
            return False, (
                f"untrusted custom code file present in snapshot: {rel} — "
                "add its SHA-256 to _UNLIMITED_OCR_TRUSTED_CODE_FILES after review, or "
                "remove the file"
            )
    return True, f"verified {len(trusted)} trusted-code files in snapshot {snap.name}"


# ── A1a: manifest-based verification ────────────────────────────────────


class TrustedManifestError(Exception):
    """Raised when the trusted manifest JSON is malformed or unloadable."""


def load_trusted_manifest(path: Path | None = None) -> dict:
    """Load and validate the runtime trusted manifest JSON.

    ``path`` defaults to the committed manifest under
    ``aksharamd.plugins.ocr_backends``. Never returns a partially
    validated dict — raises ``TrustedManifestError`` on any structural
    problem.
    """
    if path is None:
        from aksharamd.plugins.ocr_backends import (  # type: ignore[import-untyped]
            UNLIMITED_OCR_TRUSTED_MANIFEST_PATH,
        )
        path = UNLIMITED_OCR_TRUSTED_MANIFEST_PATH
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise TrustedManifestError(f"manifest missing: {path}") from e
    except json.JSONDecodeError as e:
        raise TrustedManifestError(f"manifest not valid JSON: {e}") from e
    for req in ("manifest_schema_version", "manifest_id", "repo_id", "revision", "files"):
        if req not in raw:
            raise TrustedManifestError(f"manifest missing required field: {req!r}")
    if raw["manifest_schema_version"] != _UNLIMITED_OCR_TRUSTED_MANIFEST_SUPPORTED_SCHEMA:
        raise TrustedManifestError(
            f"manifest_schema_version={raw['manifest_schema_version']!r} not supported "
            f"(expected {_UNLIMITED_OCR_TRUSTED_MANIFEST_SUPPORTED_SCHEMA})"
        )
    rev = raw["revision"]
    if not isinstance(rev, str) or len(rev) != 40 or not all(c in "0123456789abcdef" for c in rev):
        raise TrustedManifestError(f"manifest revision not a 40-char lowercase hex SHA: {rev!r}")
    files = raw["files"]
    if not isinstance(files, dict) or not files:
        raise TrustedManifestError("manifest 'files' must be a non-empty dict")
    for rel, meta in files.items():
        for k in ("sha256", "size_bytes", "class", "required_for_runtime", "verify_on_every_load"):
            if k not in meta:
                raise TrustedManifestError(f"manifest file {rel!r} missing field {k!r}")
    return raw


# Loader-relevant JSON files at the A1a-approved revision. Anything at
# the snapshot root NOT in either the manifest or this set is a
# security anomaly and refused.
_KNOWN_LOADER_JSON = {
    "config.json",
    "model.safetensors.index.json",
    "processor_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
}

# Executable extensions — any snapshot file with these NOT in the
# manifest is refused. .whl handled specially (quarantined under wheel/).
_EXECUTABLE_EXTS = {".py", ".pyd", ".so", ".dll", ".sh", ".bat", ".cmd", ".exe"}

# HF cache metadata / documentation assets — never affect verification.
_IGNORED_METADATA_EXTS = {
    ".md", ".gif", ".jpg", ".jpeg", ".png", ".pdf", ".gitattributes", ".txt", ".rst",
}


def _classify_snapshot_file(rel: str, manifest_files: set[str]) -> str:
    """Return one of ``known`` | ``refuse_executable`` | ``refuse_json`` |
    ``warn_unknown`` | ``ignore_metadata``.

    ``rel`` is the path relative to the snapshot root, using forward
    slashes.
    """
    if rel in manifest_files:
        return "known"
    name = Path(rel).name.lower()
    ext = ("." + name.rsplit(".", 1)[1]) if "." in name else ""
    if ext in _EXECUTABLE_EXTS:
        return "refuse_executable"
    if ext == ".json":
        return "refuse_json"
    if ext in _IGNORED_METADATA_EXTS or name.startswith("license"):
        return "ignore_metadata"
    return "warn_unknown"


def _canonical_containment_check(
    file_path: Path,
    cache_root: Path,
) -> tuple[bool, str]:
    """Check that ``file_path`` strictly resolves to a regular file
    inside ``cache_root``. Symlinks whose raw text contains ``..`` are
    fine as long as the canonical resolution stays inside.
    """
    try:
        resolved = file_path.resolve(strict=True)
    except FileNotFoundError:
        return False, f"broken symlink or missing file: {file_path}"
    except OSError as e:
        return False, f"symlink resolution failed for {file_path}: {e}"
    if not resolved.is_file():
        return False, f"resolved path is not a regular file: {resolved}"
    try:
        cache_root_r = cache_root.resolve(strict=True)
    except OSError as e:
        return False, f"cache root not resolvable: {cache_root} ({e})"
    try:
        resolved.relative_to(cache_root_r)
    except ValueError:
        return False, f"resolved target escapes cache root: {resolved} not under {cache_root_r}"
    return True, ""


def verify_snapshot_against_manifest(
    manifest: dict,
    snapshot_root: Path | None = None,
    *,
    hash_weights: bool = True,
) -> tuple[bool, str]:
    """Fail-closed verification of a locally cached HuggingFace snapshot
    against a runtime trusted manifest (A1a).

    Refuses on any of:

    - Snapshot missing or not at the manifest's pinned revision.
    - A manifest file missing from the snapshot.
    - SHA-256 mismatch for any file with ``verify_on_every_load: true``
      (or any file including weights when ``hash_weights=True``).
    - Size mismatch for any manifest file (catches partial download).
    - Extra executable file (``.py``, ``.pyd``, ``.so``, ``.dll``,
      ``.sh``, ``.bat``, ``.cmd``, ``.exe``) in the snapshot root not
      in the manifest.
    - Extra JSON file at the snapshot root not in the manifest.
    - Any file whose canonical resolution escapes the model cache root.
    - Any broken symlink.

    Returns ``(ok, note)``. Never raises for expected failure modes.
    """
    repo = manifest["repo_id"]
    revision = manifest["revision"]
    manifest_files = set(manifest["files"].keys())

    if snapshot_root is None:
        cache_root = Path(
            os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface" / "hub"
        )
        model_dir = cache_root / f"models--{repo.replace('/', '--')}"
        snapshots = model_dir / "snapshots"
        if not snapshots.exists():
            return False, f"no snapshots directory: {snapshots}"
        snap: Path | None = None
        for c in snapshots.iterdir():
            if c.is_dir() and c.name == revision:
                snap = c
                break
        if snap is None:
            return False, f"pinned revision {revision[:12]}... not present in local snapshots"
        model_cache_root = model_dir
    else:
        snap = snapshot_root
        parent = snap.parent
        model_cache_root = parent.parent if parent.name == "snapshots" else snap

    # Every manifest file must exist, pass canonical containment,
    # match its recorded size, and (per verify_on_every_load / hash_weights) SHA.
    for rel, meta in manifest["files"].items():
        p = snap / rel
        if not p.exists():
            return False, f"manifest file missing from snapshot: {rel}"
        ok, note = _canonical_containment_check(p, model_cache_root)
        if not ok:
            # If snapshot_root was supplied for testing and the file is
            # a regular file directly under the snap tree (no HF layout),
            # accept it as the containment root falls back to `snap`.
            try:
                p.resolve(strict=True).relative_to(snap.resolve(strict=True))
            except (ValueError, OSError):
                return False, note
        if p.stat().st_size != meta["size_bytes"]:
            return False, (
                f"size mismatch on {rel}: expected {meta['size_bytes']}, got {p.stat().st_size}"
            )
        should_hash = bool(meta.get("verify_on_every_load", False)) or (
            hash_weights and meta.get("class") == "weights"
        )
        if should_hash:
            actual = sha256_file(p)
            if actual != meta["sha256"]:
                return False, (
                    f"SHA-256 mismatch on {rel}: expected {meta['sha256'][:12]}..., "
                    f"got {actual[:12]}..."
                )

    # Exact-set validation on snapshot root. Executable or JSON files
    # not in the manifest are refused. Metadata/docs are ignored.
    # Subdirectories (e.g. wheel/) are tolerated with the same executable
    # refusal, except the intentionally quarantined .whl.
    for p in snap.iterdir():
        if p.is_dir():
            for sp in p.rglob("*"):
                if not sp.is_file():
                    continue
                rel = str(sp.relative_to(snap)).replace("\\", "/")
                cls = _classify_snapshot_file(rel, manifest_files)
                if cls == "refuse_executable":
                    # wheel/*.whl at the acquisition-inventory level is expected.
                    if p.name == "wheel" and rel.endswith(".whl"):
                        continue
                    return False, f"unreviewed executable in snapshot subdir: {rel}"
                if cls == "refuse_json":
                    return False, f"unreviewed JSON in snapshot subdir: {rel}"
            continue
        rel = p.name
        cls = _classify_snapshot_file(rel, manifest_files)
        if cls == "refuse_executable":
            return False, f"unreviewed executable in snapshot: {rel}"
        if cls == "refuse_json":
            return False, f"unreviewed JSON in snapshot (loader-relevant surface): {rel}"

    return True, (
        f"verified {len(manifest['files'])} runtime files at revision "
        f"{revision[:12]}... (manifest_id={manifest['manifest_id']})"
    )


# ── (Existing) low-level primitive kept for pre-A1a unit tests ─────────


class _UnlimitedOcrRunner:
    """Lazy-loaded singleton wrapping the Unlimited-OCR model.

    Loads the model once on first ``infer()`` call using
    ``AutoModel.from_pretrained(..., use_safetensors=True,
    torch_dtype=torch.bfloat16)`` with the pinned revision.

    Sets ``HF_HUB_OFFLINE=1`` / ``TRANSFORMERS_OFFLINE=1`` before
    importing ``transformers`` so any subsequent Hub call fails
    loudly.

    **Fail-closed security:** the load path calls
    ``verify_trusted_code_files()`` BEFORE touching
    ``AutoTokenizer.from_pretrained`` / ``AutoModel.from_pretrained``.
    If verification fails for any reason, load is refused and no
    remote code is executed.
    """

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._load_error: str = ""

    def load(self) -> None:
        if self._loaded or self._load_error:
            return
        # Enforce offline mode BEFORE transformers import.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        # ── FAIL-CLOSED: verify trusted remote code BEFORE any load. ──
        verified, note = verify_trusted_code_files(
            _UNLIMITED_OCR_MODEL_REPO,
            _UNLIMITED_OCR_MODEL_REVISION,
            _UNLIMITED_OCR_TRUSTED_CODE_FILES,
        )
        if not verified:
            self._load_error = f"trusted_code_verification_failed: {note}"
            return
        try:
            import torch  # type: ignore
            from transformers import AutoModel, AutoTokenizer  # type: ignore
        except ImportError as e:
            self._load_error = f"import_failed: {e}"
            return
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                _UNLIMITED_OCR_MODEL_REPO,
                revision=_UNLIMITED_OCR_MODEL_REVISION,
                trust_remote_code=True,
                local_files_only=True,
            )
            self._model = AutoModel.from_pretrained(
                _UNLIMITED_OCR_MODEL_REPO,
                revision=_UNLIMITED_OCR_MODEL_REVISION,
                trust_remote_code=True,
                use_safetensors=True,
                torch_dtype=torch.bfloat16,
                local_files_only=True,
            ).eval().cuda()
            self._loaded = True
        except Exception as e:
            self._load_error = f"load_failed: {type(e).__name__}: {e}"[:400]

    def infer_pdf(self, pdf: Path, workdir: Path, max_length: int = 32768) -> tuple[str, str, dict[str, Any]]:
        """Return ``(text, exception_or_empty, tool_signals)``.

        Per-asset scratch (rendered PNGs + model output) lives in a
        ``TemporaryDirectory`` that is removed after this call returns —
        success OR exception. This prevents multi-gigabyte accumulation
        on long documents (300-DPI PNGs × N pages).
        """
        if not self._loaded:
            self.load()
        if not self._loaded:
            return "", self._load_error, {}
        try:
            import torch  # type: ignore
        except ImportError as e:
            return "", f"torch_import_failed: {e}", {}
        # TemporaryDirectory context guarantees cleanup on both the
        # success and exception paths.
        with tempfile.TemporaryDirectory(prefix=f"unlimited_ocr_{pdf.stem}_",
                                          dir=str(workdir)) as scratch_str:
            scratch = Path(scratch_str)
            try:
                page_dir = scratch / "pages"
                page_dir.mkdir(parents=True, exist_ok=True)
                image_paths = _pdf_to_page_images(pdf, page_dir, dpi=300)
                # Fresh output directory — never reused between the
                # primary parse and any deterministic recompile.
                out_dir = scratch / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                if torch.cuda.is_available():
                    torch.cuda.reset_peak_memory_stats(0)
                assert self._model is not None and self._tokenizer is not None  # nosec B101 - narrows mypy Optional
                result = self._model.infer_multi(
                    self._tokenizer,
                    prompt="<image>Multi page parsing.",
                    image_files=[str(p) for p in image_paths],
                    output_path=str(out_dir),
                    image_size=1024,
                    max_length=max_length,
                    no_repeat_ngram_size=35,
                    ngram_window=1024,
                    save_results=True,
                )
                # infer_multi writes a Markdown file into out_dir; read
                # BEFORE the temp dir is torn down.
                md_files = sorted(out_dir.glob("*.md"))
                text = "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in md_files)
                if not text and isinstance(result, str):
                    text = result
                peak_mib: int | None = None
                if torch.cuda.is_available():
                    peak_mib = int(torch.cuda.max_memory_allocated(0) // (1024 * 1024))
                return text, "", {
                    "page_count": len(image_paths),
                    "peak_gpu_memory_mib": peak_mib,
                    "output_files_written": len(md_files),
                }
            except Exception as e:
                return "", f"infer_failed: {type(e).__name__}: {e}"[:400], {}


_RUNNER = _UnlimitedOcrRunner()


# ── Adapter ──────────────────────────────────────────────────────────────


def _load_manifest() -> dict[str, Any]:
    if not _MANIFEST.exists():
        raise RuntimeError(f"manifest not present: {_MANIFEST}")
    with _MANIFEST.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dependency_versions() -> dict[str, str]:
    keys = ("torch", "transformers", "pymupdf", "einops", "addict",
            "easydict", "Pillow", "aksharamd")
    out: dict[str, str] = {}
    for k in keys:
        try:
            from importlib.metadata import version
            out[k] = version(k)
        except Exception:
            out[k] = "unknown"
    return out


def _decide_execution_mode(
    forced_real: bool,
    forced_dry_run: bool,
    gpu: dict[str, Any],
) -> tuple[str, str]:
    """Return ``(mode, note)`` — the mode to run in and a human-readable
    reason.

    - ``real_inference`` — run the model.
    - ``dry_run`` — skip inference; emit adapter-shape record only.
    - ``model_not_cached`` — the pinned model is not in the local cache
      and no ``--allow-download`` was passed. Falls through to dry-run
      per asset with a distinct execution_mode string.
    """
    ok, reason = _pinned_deps_present()
    if not ok:
        return "deps_missing", reason
    if forced_dry_run:
        return "dry_run", "--dry-run explicitly requested"
    if not gpu.get("cuda_available"):
        return "no_gpu", "CUDA not available; refuse real inference"
    if not gpu.get("bf16_supported"):
        return "no_gpu", "BF16 not supported on this device"
    cached, cache_reason = _model_cached_locally(
        _UNLIMITED_OCR_MODEL_REPO, _UNLIMITED_OCR_MODEL_REVISION
    )
    if not cached:
        if forced_real:
            return "model_not_cached", cache_reason
        return "model_not_cached", cache_reason
    return "real_inference", "model cached, GPU + BF16 available"


def run_one(
    asset: dict[str, Any],
    *,
    execution_mode: str,
    workdir: Path,
    do_deterministic_check: bool,
    human_reviews: dict[str, dict[str, str]] | None,
) -> RunResult:
    aid = asset["asset_id"]
    pdf = Path(asset["pdf_path"])

    text = ""
    exc = ""
    elapsed = 0.0
    tool_signals: dict[str, Any] = {}
    peak_mib: int | None = None

    if execution_mode == "real_inference":
        t0 = time.perf_counter()
        text, exc, tool_signals = _RUNNER.infer_pdf(pdf, workdir)
        elapsed = time.perf_counter() - t0
        peak_mib = tool_signals.get("peak_gpu_memory_mib")
    else:
        exc = f"skipped: execution_mode={execution_mode}"

    execution_success = (exc == "" and text is not None)
    doc_md = text or ""
    output_package_created = execution_success and bool(doc_md)
    non_ws = sum(1 for c in doc_md if not c.isspace())
    output_chars = len(doc_md)
    tokens = _estimate_tokens(doc_md)
    size_bytes = int(asset.get("size_bytes") or 0)
    inflation = (output_chars / size_bytes) if size_bytes else 0.0

    near_empty_equivalent = non_ws < 50
    low_density_equivalent = (
        size_bytes > 0 and inflation < 0.0005 and non_ws < 400
    )

    content_extracted = (
        output_package_created
        and non_ws >= _MIN_MEANINGFUL_CHARS
        and not near_empty_equivalent
    )

    repeat_ratio = _repeat_content_ratio(doc_md)
    repeat_gate_ok = (len(doc_md.split()) < 100 or repeat_ratio < 0.50)
    has_text_layer, hidden_text_chars = _hidden_text_layer_chars(pdf)
    structurally_usable = (
        content_extracted
        and repeat_gate_ok
        and not (low_density_equivalent and (has_text_layer is not False))
    )

    deterministic: bool | None = None
    if execution_mode == "real_inference" and output_package_created and do_deterministic_check:
        text2, _exc2, _sig2 = _RUNNER.infer_pdf(pdf, workdir)
        deterministic = (doc_md == text2)

    review = (human_reviews or {}).get(aid, {})
    review_status = "reviewed" if review else "not_reviewed"

    return RunResult(
        asset_id=aid,
        corpus_source=asset.get("corpus_source", ""),
        document_class=asset.get("document_class", "unknown"),
        execution_success=execution_success,
        execution_mode=execution_mode,
        exception=exc,
        output_package_created=output_package_created,
        content_extracted=content_extracted,
        structurally_usable=structurally_usable,
        human_review_status=review_status,
        human_usability=review.get("usability", "not_reviewed"),
        human_review_evidence=review.get("evidence", ""),
        runtime_seconds=round(elapsed, 3),
        output_chars=output_chars,
        non_whitespace_chars=non_ws,
        estimated_tokens=tokens,
        output_size_inflation=round(inflation, 4),
        deterministic=deterministic,
        page_count_pdf=asset.get("page_count"),
        hidden_text_layer=has_text_layer,
        hidden_text_layer_chars=hidden_text_chars,
        image_placeholder_ratio=_image_placeholder_ratio(doc_md),
        repeat_content_ratio=round(repeat_ratio, 4),
        near_empty_equivalent=near_empty_equivalent,
        low_density_equivalent=low_density_equivalent,
        peak_gpu_memory_mib=peak_mib,
        tool_signals=tool_signals,
    )


# ── Aggregation (subset — full report focuses on execution modes) ──────


_NOT_EXECUTED_MODES = {"dry_run", "model_not_cached", "no_gpu", "deps_missing"}


def _bucket(rows: list[RunResult]) -> dict[str, Any]:
    n = len(rows)
    exec_ok = sum(1 for r in rows if r.execution_success)
    content = sum(1 for r in rows if r.content_extracted)
    struct = sum(1 for r in rows if r.structurally_usable)
    runtimes = [r.runtime_seconds for r in rows if r.execution_success and r.runtime_seconds > 0]
    gpu_peaks = [r.peak_gpu_memory_mib for r in rows if r.peak_gpu_memory_mib is not None]
    review_rows = [r for r in rows if r.human_review_status == "reviewed"]
    usable = sum(1 for r in review_rows if r.human_usability in _USABLE_ENUM)
    modes: dict[str, int] = {}
    for r in rows:
        modes[r.execution_mode] = modes.get(r.execution_mode, 0) + 1
    # Distinguish "did not attempt inference" (dry-run / no-model /
    # no-gpu / deps-missing) from "attempted inference and failed"
    # (real_inference with execution_success=False). The dry-run
    # artifact should NOT be read as 45 execution failures.
    not_executed = sum(1 for r in rows if r.execution_mode in _NOT_EXECUTED_MODES)
    real_attempts = n - not_executed
    real_failures = sum(1 for r in rows
                        if r.execution_mode == "real_inference" and not r.execution_success)
    return {
        "n": n,
        "not_executed_count": not_executed,
        "not_executed_rate": round(not_executed / n, 4) if n else 0.0,
        "real_inference_attempts": real_attempts,
        "real_inference_failures": real_failures,
        "execution_success_count": exec_ok,
        "execution_success_rate": round(exec_ok / n, 4) if n else 0.0,
        "content_extracted_count": content,
        "meaningful_content_rate": round(content / n, 4) if n else 0.0,
        "structurally_usable_count": struct,
        "structurally_usable_rate": round(struct / n, 4) if n else 0.0,
        "runtime_seconds_p50": round(statistics.median(runtimes), 3) if runtimes else None,
        "runtime_seconds_p95": round(max(runtimes), 3) if runtimes else None,
        "peak_gpu_memory_mib_max": max(gpu_peaks) if gpu_peaks else None,
        "human_reviewed_count": len(review_rows),
        "human_usable_count": usable,
        "human_usable_rate": round(usable / len(review_rows), 4) if review_rows else None,
        "execution_mode_counts": modes,
    }


def _aggregate(results: list[RunResult]) -> dict[str, Any]:
    ag: dict[str, Any] = {"overall": _bucket(results)}
    ag["by_document_class"] = {
        c: _bucket([r for r in results if r.document_class == c])
        for c in sorted({r.document_class for r in results})
    }
    ag["execution_failures"] = [
        {"asset_id": r.asset_id, "exception": r.exception, "mode": r.execution_mode}
        for r in results if not r.execution_success
    ]
    return ag


# ── Report + orchestration ─────────────────────────────────────────────


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _render_report(manifest: dict, results: list[RunResult], aggregate: dict, path: Path,
                    gpu_report: dict, tool_version: str, executed_mode_summary: dict[str, int]) -> None:
    L: list[str] = []
    ov = aggregate["overall"]

    def add(s: str = "") -> None:
        L.append(s)

    add(f"# PDF Benchmark v1 — Unlimited-OCR adapter ({time.strftime('%Y-%m-%d')})")
    add()
    add(f"**Tool:** Baidu Unlimited-OCR (HuggingFace `{_UNLIMITED_OCR_MODEL_REPO}`)")
    add(f"**Pinned revision:** `{_UNLIMITED_OCR_MODEL_REVISION or 'NOT SET — adapter refuses to load'}`")
    add(f"**Commit under evaluation:** `{manifest['commit_under_evaluation']}`")
    add(f"**Python:** {sys.version.split()[0]} · **Platform:** {platform.platform()}")
    add()
    add("**No AksharaMD production code changes.** `SCORING_POLICY_VERSION` remains `\"1.0\"`.")
    add()
    add("## Environment feasibility")
    add()
    add(f"- torch: {tool_version}")
    add(f"- CUDA available: {gpu_report.get('cuda_available')}")
    if gpu_report.get("cuda_available"):
        add(f"- Device: {gpu_report.get('device_0_name')} ({gpu_report.get('device_0_vram_gib')} GB VRAM)")
        add(f"- Compute capability: {gpu_report.get('device_0_compute_capability')}")
        add(f"- BF16 supported: {gpu_report.get('bf16_supported')}")
    add()
    add("## Execution mode summary")
    add()
    for mode, count in sorted(executed_mode_summary.items()):
        add(f"- `{mode}`: {count}")
    add()
    add("If `real_inference` is not present, the benchmark did NOT run the model on any file. Real inference requires: pinned revision configured, model downloaded to the local HuggingFace cache, NVIDIA GPU with BF16 support. See the ADR (`docs/adr/ocr_backend_strategy.md`) for the download procedure.")
    add()

    add("## Headline metrics")
    add()
    add("| Metric | Value |")
    add("|---|---:|")
    add(f"| Files evaluated | {ov['n']} |")
    add(f"| `execution_success_rate` | {ov['execution_success_count']} / {ov['n']} ({ov['execution_success_rate'] * 100:.1f} %) |")
    add(f"| `meaningful_content_rate` | {ov['content_extracted_count']} / {ov['n']} ({ov['meaningful_content_rate'] * 100:.1f} %) |")
    add(f"| `structurally_usable_rate` | {ov['structurally_usable_count']} / {ov['n']} ({ov['structurally_usable_rate'] * 100:.1f} %) |")
    if ov["runtime_seconds_p50"] is not None:
        add(f"| Runtime p50 / max (s) | {ov['runtime_seconds_p50']} / {ov['runtime_seconds_p95']} |")
    if ov.get("peak_gpu_memory_mib_max"):
        add(f"| Peak GPU memory observed (MiB) | {ov['peak_gpu_memory_mib_max']} |")
    add()

    if ov["execution_mode_counts"].get("real_inference", 0) == 0:
        add("## Interpretation — evidence pending")
        add()
        add("This report was generated in `dry_run` / `model_not_cached` / `no_gpu` / `deps_missing` mode. The adapter, tests, and benchmark harness are in place, but real inference against the 45-asset corpus requires the ~14 GB `baidu/Unlimited-OCR` model download.")
        add()
        add("The paired human review vs. AksharaMD Phase 1 and vs. the other three adapters is deferred until real inference has run against every eligible asset.")
        add()

    add("## Constraints observed")
    add()
    add("- No AksharaMD parser / validator / scoring / warning-penalty / packaging / model code changed.")
    add("- `SCORING_POLICY_VERSION` remains `\"1.0\"`.")
    add("- Same 45-asset frozen manifest as AksharaMD Phase 1 and all other adapters.")
    add("- Same checksum-verified ParseBench cache.")
    add("- Offline enforcement: `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` set before `transformers` import.")
    add("- `use_safetensors=True` — refuses pickle-based weights.")
    add("- `trust_remote_code=True` gated on a PINNED revision — no mutable branch reference accepted.")
    add("- Model download NOT performed by this adapter.")
    add("- Per-file errors preserved; single failures do not abort the run.")
    add("- No cross-parser ranking or winner declaration.")
    add()

    path.write_text("\n".join(L), encoding="utf-8")


def _run(
    output_json: Path,
    output_md: Path,
    *,
    forced_real: bool,
    forced_dry_run: bool,
    only: str | None,
    do_deterministic_check: bool,
    human_reviews: dict[str, dict[str, str]] | None,
) -> int:
    manifest = _load_manifest()
    assets = [a for a in manifest["assets"] if a["eligibility"] == "eligible"]
    if only:
        assets = [a for a in assets if a["asset_id"] == only or a["asset_id"].endswith(only)]
        if not assets:
            print(f"--only {only!r} matched no assets", file=sys.stderr)
            return 43

    gpu = _gpu_capability_report()
    mode, mode_note = _decide_execution_mode(forced_real, forced_dry_run, gpu)
    print(f"execution_mode={mode} ({mode_note})", file=sys.stderr)

    tool_version = gpu.get("torch_version", "unknown")

    results: list[RunResult] = []
    # TemporaryDirectory context so the benchmark scratch space is
    # removed after every run — regardless of exceptions.
    with tempfile.TemporaryDirectory(prefix="unlimited_ocr_bench_") as workdir_str:
        workdir = Path(workdir_str)
        for a in sorted(assets, key=lambda a: a["asset_id"]):
            print(f"running {a['asset_id']} [{mode}]", file=sys.stderr)
            results.append(run_one(
                a,
                execution_mode=mode,
                workdir=workdir,
                do_deterministic_check=do_deterministic_check,
                human_reviews=human_reviews,
            ))

    aggregate = _aggregate(results)
    executed_mode_summary = aggregate["overall"]["execution_mode_counts"]

    payload = {
        "harness_version": "unlimited_ocr_adapter.py@2026-07-20",
        "adapter_target": "unlimited-ocr",
        "adapter_target_repo": _UNLIMITED_OCR_MODEL_REPO,
        "adapter_target_revision": _UNLIMITED_OCR_MODEL_REVISION,
        "manifest_source": _MANIFEST.name,
        "manifest_commit_under_evaluation": manifest.get("commit_under_evaluation"),
        "run_started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "gpu_report": gpu,
        "execution_mode_decision": {"mode": mode, "note": mode_note},
        "dependencies": _dependency_versions(),
        "evaluation_semantics_notes": {
            "aksharamd_readiness_score_used": False,
            "aksharamd_warning_codes_used": False,
            "near_empty_equivalent_definition": "non-whitespace chars < 50",
            "low_density_equivalent_definition": "output_size_inflation < 0.0005 AND non_whitespace_chars < 400",
            "no_cross_parser_ranking": True,
        },
        "security_notes": {
            "trust_remote_code": True,
            "revision_pinned": _UNLIMITED_OCR_MODEL_REVISION is not None,
            "safetensors_only": True,
            "offline_enforcement": {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            },
            "trusted_code_files_verified": bool(_UNLIMITED_OCR_TRUSTED_CODE_FILES),
        },
        "aggregate": aggregate,
        "per_asset": [asdict(r) for r in results],
    }
    _write_json(output_json, payload)
    print(f"wrote {output_json}", file=sys.stderr)

    _render_report(manifest, results, aggregate, output_md,
                    gpu_report=gpu, tool_version=tool_version,
                    executed_mode_summary=executed_mode_summary)
    print(f"wrote {output_md}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-json", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20.json")
    ap.add_argument("--output-md", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20.md")
    ap.add_argument("--only", type=str, default=None,
                    help="Only run assets whose id matches this suffix")
    ap.add_argument("--real", action="store_true",
                    help="Attempt real inference (requires model in local cache)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Skip inference; emit adapter-shape record only")
    ap.add_argument("--no-deterministic-check", action="store_true")
    ap.add_argument("--human-reviews", type=Path, default=None,
                    help="Path to JSON dict {asset_id: {usability, evidence}}")
    args = ap.parse_args()
    if args.real and args.dry_run:
        print("error: --real and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    reviews: dict[str, dict[str, str]] | None = None
    if args.human_reviews is not None:
        with args.human_reviews.open("r", encoding="utf-8") as f:
            reviews = json.load(f)
    return _run(
        args.output_json,
        args.output_md,
        forced_real=args.real,
        forced_dry_run=args.dry_run,
        only=args.only,
        do_deterministic_check=not args.no_deterministic_check,
        human_reviews=reviews,
    )


# ── Utility: hash a file (for trusted-code verification) ────────────


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
