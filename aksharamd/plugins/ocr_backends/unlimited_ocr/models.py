"""Lifecycle module for the Unlimited-OCR model snapshot (PR 98).

Provides the four operations backing the ``aksharamd models`` CLI
subcommands:

- :func:`get_model_status` — read-only inspection (no lock, no network).
- :func:`install_model` — atomic download + verify + promote.
- :func:`verify_model` — network-free byte verification via
  :mod:`aksharamd.plugins.ocr_backends.verification_receipt`.
- :func:`remove_model` — atomic removal of the pinned snapshot and
  its blobs (never touches other cache entries).

Only one model is currently supported: ``unlimited_ocr``. Additional
models would extend this module rather than duplicating it.

## Safety invariants

1. NO model download during ``import aksharamd``. All heavy imports
   (huggingface_hub.snapshot_download in particular) live inside
   :func:`install_model` and only fire when that function is called.
2. NO download during ``doctor`` or during normal ``compile``.
3. The revision passed to ``snapshot_download`` is ALWAYS the pinned
   value from :data:`_UNLIMITED_OCR_MODEL_REVISION` in
   ``adapter.py`` — never a user-supplied value.
4. ``install`` never marks itself successful before byte verification
   passes.
5. On any install failure the staging directory is deleted; any
   partial write at the final destination is removed. A previously
   verified snapshot at the final destination is never overwritten
   until verification passes on the new staging area.
6. ``trust_remote_code`` is never set — ``snapshot_download`` does
   not accept it and we never execute code from the downloaded
   snapshot inside this module.
7. Disk-full and network-interrupt errors surface as categorical
   :class:`InstallOutcome` values so callers render actionable text
   rather than raw tracebacks.
8. HuggingFace error strings are sanitized before being surfaced to
   the user — no tokens, no signed URLs, no full env dumps.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .. import UNLIMITED_OCR_TRUSTED_MANIFEST_PATH
from ..verification_receipt import (
    full_verify_and_write_receipt,
    invalidate_receipt,
    receipt_path,
)
from ._lock import LockHeld, model_lock

_logger = logging.getLogger(__name__)

# The ONLY model name the lifecycle module accepts. Any other name
# must be rejected at the CLI boundary with EXIT_INVALID_COMMAND (2).
MODEL_NAME = "unlimited_ocr"

# 500 MiB headroom on top of the expected download size. Downloads
# tend to unpack to slightly more than the sum of file sizes (HF
# stores blobs then hardlinks), and users need working space for
# subsequent verify + normal use.
_DISK_HEADROOM_BYTES = 500 * 1024 * 1024

# Bytes-in-a-GiB helper for user-facing render.
_GIB = 1024 ** 3
_MIB = 1024 ** 2

# Static fallback estimate when the manifest lacks size_bytes fields
# (defensive — the shipped manifest has them, so this is only
# exercised in tests). Roughly the observed 6.72 GB total for the
# pinned revision.
_STATIC_DOWNLOAD_SIZE_ESTIMATE_BYTES = int(6.75 * _GIB)

_LICENSE_NOTICE = (
    "Baidu Unlimited-OCR model. The model weights are distributed by "
    "Baidu under the license posted on its HuggingFace repository "
    "(https://huggingface.co/baidu/Unlimited-OCR). By installing the "
    "model you agree to that license. AksharaMD does not sublicense "
    "the model — it only downloads it from the upstream repository."
)


# ── Exit codes ─────────────────────────────────────────────────────────────

EXIT_OK = 0
EXIT_OPERATION_FAILURE = 1
EXIT_INVALID_COMMAND = 2
EXIT_HARDWARE_INCOMPATIBLE = 3
EXIT_INSUFFICIENT_DISK = 4
EXIT_DOWNLOAD_FAILURE = 5
EXIT_VERIFICATION_FAILURE = 6


# ── Dataclasses ────────────────────────────────────────────────────────────


DownloadSizeSource = Literal["manifest", "static_estimate", "unknown"]


@dataclass
class ModelInfo:
    """Static (never-changes-at-runtime) information about a model."""

    name: str
    repo_id: str
    revision: str
    download_size_bytes: int | None
    download_size_source: DownloadSizeSource
    license_notice: str
    snapshot_path: Path | None


@dataclass
class ModelStatus:
    """Complete runtime status of a model.

    Extends the ``BackendAvailabilityDetails`` schema with lifecycle
    signals (byte verification, exact-reason narrative). Field names
    are stable — the JSON shape appears in ``aksharamd models status
    --json`` and consumers pin against it.
    """

    name: str
    repo_id: str
    revision: str
    download_size_bytes: int | None
    download_size_source: DownloadSizeSource
    snapshot_present: bool
    manifest_present: bool
    byte_verified: bool
    hardware_compatible: bool | None
    runnable_now: bool
    snapshot_path: Path | None
    receipt_path: Path | None
    reason: str
    # Verbatim details from the backend's availability() probe when
    # the model_installed=True path is exercised. Empty otherwise.
    availability_details: dict[str, Any] = field(default_factory=dict)


@dataclass
class InstallOutcome:
    """Result of an install operation."""

    status: Literal[
        "ok",
        "already_installed",
        "invalid_command",
        "hardware_incompatible",
        "insufficient_disk",
        "download_failure",
        "verification_failure",
        "operation_failure",
    ]
    note: str
    exit_code: int
    snapshot_path: Path | None = None
    receipt_path: Path | None = None
    bytes_downloaded: int | None = None


@dataclass
class VerifyOutcome:
    """Result of a verify operation."""

    ok: bool
    note: str
    exit_code: int
    receipt_path: Path | None = None
    files_hashed: list[str] = field(default_factory=list)


@dataclass
class RemoveOutcome:
    """Result of a remove operation."""

    status: Literal["ok", "already_absent", "operation_failure"]
    note: str
    exit_code: int
    bytes_recovered: int = 0
    snapshot_removed: bool = False
    blobs_removed: int = 0
    runtime_cache_cleared: bool = False


# ── Path helpers ───────────────────────────────────────────────────────────


def _hf_cache_root() -> Path:
    """Return the effective HuggingFace cache root.

    Honors ``HF_HOME`` (aligned with
    :mod:`~aksharamd.plugins.ocr_backends.verification_receipt`); falls
    back to ``~/.cache/huggingface/hub``. This must match what
    ``huggingface_hub.snapshot_download`` uses — otherwise the receipt
    system would resolve a different snapshot than the download.
    """
    env = os.environ.get("HF_HOME")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "huggingface" / "hub"


def _repo_slug(repo_id: str) -> str:
    return repo_id.replace("/", "--")


def _repo_root(cache_root: Path, repo_id: str) -> Path:
    return cache_root / f"models--{_repo_slug(repo_id)}"


def _snapshot_dir(cache_root: Path, repo_id: str, revision: str) -> Path:
    return _repo_root(cache_root, repo_id) / "snapshots" / revision


def _lock_path(cache_root: Path, repo_id: str) -> Path:
    """Lock file lives beside the model's cache dir, on the same
    volume, so an interrupted install cannot leak a lock into a
    different volume that a subsequent install would not see."""
    return _repo_root(cache_root, repo_id).parent / (
        f"models--{_repo_slug(repo_id)}.aksharamd.lock"
    )


def _staging_dir(cache_root: Path, repo_id: str, revision: str) -> Path:
    """Staging directory: same parent as the final snapshot dir so
    ``os.replace()`` between them is an atomic rename inside a single
    volume on both POSIX and Windows.

    We use a fixed name derived from the revision so a crashed
    install leaves a predictable directory an operator can delete
    (or the next install cleans up automatically).
    """
    return _repo_root(cache_root, repo_id) / "snapshots" / (
        f"aksharamd_staging_{revision}"
    )


# ── Manifest / adapter helpers ─────────────────────────────────────────────


def _load_pinned_reference() -> tuple[str, str]:
    """Return ``(repo_id, revision)`` from the adapter's pinned
    constants. Import is done at call time so ``import aksharamd``
    does not touch the heavier adapter module."""
    from .adapter import _UNLIMITED_OCR_MODEL_REPO, _UNLIMITED_OCR_MODEL_REVISION
    if not _UNLIMITED_OCR_MODEL_REVISION:
        raise RuntimeError(
            "adapter has no pinned revision; refusing to install "
            "an unpinned model"
        )
    return _UNLIMITED_OCR_MODEL_REPO, _UNLIMITED_OCR_MODEL_REVISION


def _load_manifest() -> tuple[dict, Path]:
    """Load the trusted manifest and return (manifest_dict, manifest_path)."""
    from .adapter import load_trusted_manifest
    manifest = load_trusted_manifest()
    return manifest, UNLIMITED_OCR_TRUSTED_MANIFEST_PATH


def _manifest_expected_download_bytes(manifest: dict) -> tuple[int | None, DownloadSizeSource]:
    """Sum the ``size_bytes`` field across manifest entries.

    Returns ``(None, "static_estimate")`` if any entry lacks
    ``size_bytes`` — the manifest schema requires the field so this
    branch is defensive (test fixtures may omit it).
    """
    try:
        total = 0
        for _rel, meta in manifest["files"].items():
            sz = meta.get("size_bytes")
            if sz is None or not isinstance(sz, int) or sz < 0:
                return _STATIC_DOWNLOAD_SIZE_ESTIMATE_BYTES, "static_estimate"
            total += sz
        return total, "manifest"
    except (KeyError, TypeError):
        return _STATIC_DOWNLOAD_SIZE_ESTIMATE_BYTES, "static_estimate"


# ── Sanitization ───────────────────────────────────────────────────────────


_TOKEN_PATTERN = re.compile(
    r"(hf_[A-Za-z0-9]{6,})|(Bearer\s+[A-Za-z0-9\-._~+/]+=*)|"
    r"(X-Amz-[A-Za-z-]+=[^&\s]+)|(sig=[^&\s]+)|(token=[^&\s]+)",
    re.IGNORECASE,
)


def _sanitize_error_message(msg: str) -> str:
    """Strip tokens / signed URLs from a message before surfacing it."""
    if not msg:
        return ""
    cleaned = _TOKEN_PATTERN.sub("<redacted>", msg)
    # Truncate long strings — keeps logs bounded for pathological
    # tracebacks from huggingface_hub.
    if len(cleaned) > 1_500:
        cleaned = cleaned[:1_500] + " …[truncated]"
    return cleaned


# ── Public read-only API ───────────────────────────────────────────────────


def get_model_info() -> ModelInfo:
    """Return static info about the model. No lock, no network."""
    repo_id, revision = _load_pinned_reference()
    cache_root = _hf_cache_root()
    snap = _snapshot_dir(cache_root, repo_id, revision)
    try:
        manifest, _ = _load_manifest()
        expected, source = _manifest_expected_download_bytes(manifest)
    except Exception:
        expected, source = (_STATIC_DOWNLOAD_SIZE_ESTIMATE_BYTES, "static_estimate")
    return ModelInfo(
        name=MODEL_NAME,
        repo_id=repo_id,
        revision=revision,
        download_size_bytes=expected,
        download_size_source=source,
        license_notice=_LICENSE_NOTICE,
        snapshot_path=snap if snap.is_dir() else None,
    )


def get_model_status() -> ModelStatus:
    """Return a full status report for the model. No lock, no network.

    Byte verification runs the fast path (or falls back to a cheap
    receipt-existence probe) — it does NOT re-hash the 6.6 GB shard
    on every ``status`` call. Callers that want a fresh byte-hash
    should use :func:`verify_model` explicitly.
    """
    repo_id, revision = _load_pinned_reference()
    cache_root = _hf_cache_root()
    snap = _snapshot_dir(cache_root, repo_id, revision)
    snapshot_present = snap.is_dir()

    manifest_present = UNLIMITED_OCR_TRUSTED_MANIFEST_PATH.exists()
    expected_bytes: int | None
    size_source: DownloadSizeSource
    try:
        manifest, _manifest_path = _load_manifest()
        expected_bytes, size_source = _manifest_expected_download_bytes(manifest)
    except Exception as exc:
        _logger.debug("status: manifest load failed: %s", exc)
        manifest = None
        expected_bytes = None
        size_source = "unknown"

    # Byte-verification signal: receipt exists AND matches the manifest.
    byte_verified = False
    rpath: Path | None = None
    if manifest is not None:
        try:
            rpath = receipt_path(manifest, cache_root)
            byte_verified = rpath.exists()
        except Exception as exc:
            _logger.debug("status: receipt_path failed: %s", exc)

    # Hardware compatibility comes from the backend probe. We keep it
    # optional so status remains cheap on machines without torch.
    hw_compatible: bool | None = None
    runnable_now = False
    reason = ""
    details_out: dict[str, Any] = {}
    try:
        from ..unlimited_ocr_backend import UnlimitedOcrBackend
        backend = UnlimitedOcrBackend()
        avail = backend.availability()
        hw_compatible = bool(avail.hardware_compatible)
        runnable_now = bool(avail.is_available)
        reason = avail.reason or ""
        if avail.details is not None:
            from dataclasses import asdict
            details_out = {
                k: v for k, v in asdict(avail.details).items() if v is not None
            }
    except Exception as exc:
        _logger.debug("status: backend availability probe failed: %s", exc)
        reason = f"backend probe unavailable: {type(exc).__name__}"

    # Even without a working backend, we can still describe the model
    # state ("snapshot absent — run `aksharamd models install`").
    if not reason:
        if not manifest_present:
            reason = "trusted manifest missing from installation"
        elif not snapshot_present:
            reason = (
                "model snapshot not present at pinned revision; run "
                "`aksharamd models install unlimited_ocr` to download it"
            )
        elif not byte_verified:
            reason = (
                "snapshot present but no verification receipt; run "
                "`aksharamd models verify unlimited_ocr` to hash it"
            )
        elif not runnable_now:
            reason = "model installed and verified"

    return ModelStatus(
        name=MODEL_NAME,
        repo_id=repo_id,
        revision=revision,
        download_size_bytes=expected_bytes,
        download_size_source=size_source,
        snapshot_present=snapshot_present,
        manifest_present=manifest_present,
        byte_verified=byte_verified,
        hardware_compatible=hw_compatible,
        runnable_now=runnable_now,
        snapshot_path=snap if snapshot_present else None,
        receipt_path=rpath if rpath is not None and rpath.exists() else None,
        reason=reason,
        availability_details=details_out,
    )


# ── Install ────────────────────────────────────────────────────────────────


def _preflight_hardware(cache_root: Path) -> tuple[bool, str]:
    """Cheap hardware compatibility probe using the backend's own
    ``availability()`` method. We deliberately IGNORE the model-
    installed and snapshot-cached signals here — those failures are
    exactly what install is meant to fix.

    Returns ``(ok, reason)``.
    """
    try:
        from ..unlimited_ocr_backend import UnlimitedOcrBackend
        backend = UnlimitedOcrBackend()
        avail = backend.availability()
    except Exception as exc:
        # If the backend cannot even be constructed, treat as hardware
        # incompatible — the user cannot use it either way.
        return False, f"backend probe raised {type(exc).__name__}: {exc}"
    if not avail.hardware_compatible:
        return False, avail.reason or "hardware not compatible"
    return True, ""


def _preflight_disk(cache_root: Path, need_bytes: int) -> tuple[bool, str, int]:
    """Return ``(ok, reason, free_bytes)`` for ``cache_root``'s volume.

    Uses the parent that actually exists (creating the cache dir is
    part of install; ``disk_usage`` needs an extant path).
    """
    probe = cache_root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(str(probe))
    except OSError as exc:
        return False, f"disk_usage probe failed: {exc}", 0
    free = int(usage.free)
    required = need_bytes + _DISK_HEADROOM_BYTES
    if free < required:
        return (
            False,
            (
                f"insufficient free disk: {free // _MIB} MiB free, "
                f"{required // _MIB} MiB required "
                f"(download {need_bytes // _MIB} MiB + "
                f"{_DISK_HEADROOM_BYTES // _MIB} MiB headroom)"
            ),
            free,
        )
    return True, "", free


def _remove_tree_best_effort(path: Path) -> None:
    """Delete a directory tree, ignoring errors on individual entries.

    On Windows, files can be held open by a virus scanner briefly
    after the process closes them; we do NOT retry loops here (that
    is caller policy) — we just tolerate incomplete removal so a
    subsequent install can retry.
    """
    if not path.exists():
        return
    try:
        shutil.rmtree(str(path), ignore_errors=True)
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("failed to remove tree %s: %s", path, exc)


def install_model(
    *,
    assume_yes: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> InstallOutcome:
    """Install the Unlimited-OCR model snapshot.

    The confirmation prompt itself lives in the CLI layer. This
    function takes ``assume_yes`` for API-parity so a caller can
    invoke the same lifecycle without going through Click.

    ``progress_callback`` receives short human-readable phase names
    ("preflight", "downloading", "verifying", "promoting") for
    diagnostic surfaces. Never called with anything that would
    surprise a log-scraper.
    """
    _ = assume_yes  # confirmation happens in the CLI shell

    def _emit(phase: str) -> None:
        if progress_callback is not None:
            try:
                progress_callback(phase)
            except Exception:  # pragma: no cover - never let telemetry break install
                _logger.debug("progress_callback raised", exc_info=True)

    _emit("preflight")

    # ── Load static references (fast, no network) ─────────────────
    try:
        repo_id, revision = _load_pinned_reference()
    except Exception as exc:
        return InstallOutcome(
            status="operation_failure",
            note=f"cannot resolve pinned model reference: {exc}",
            exit_code=EXIT_OPERATION_FAILURE,
        )

    try:
        manifest, manifest_path = _load_manifest()
    except Exception as exc:
        return InstallOutcome(
            status="operation_failure",
            note=f"cannot load trusted manifest: {exc}",
            exit_code=EXIT_OPERATION_FAILURE,
        )

    expected_bytes, _size_source = _manifest_expected_download_bytes(manifest)
    if expected_bytes is None:
        expected_bytes = _STATIC_DOWNLOAD_SIZE_ESTIMATE_BYTES

    cache_root = _hf_cache_root()
    final_snap = _snapshot_dir(cache_root, repo_id, revision)
    staging = _staging_dir(cache_root, repo_id, revision)
    lock_file = _lock_path(cache_root, repo_id)

    # Ensure the cache root exists (creating it now so disk_usage +
    # lock creation both work).
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        _repo_root(cache_root, repo_id).mkdir(parents=True, exist_ok=True)
        (_repo_root(cache_root, repo_id) / "snapshots").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return InstallOutcome(
            status="operation_failure",
            note=f"cannot create cache directory: {exc}",
            exit_code=EXIT_OPERATION_FAILURE,
        )

    # ── Hardware preflight ─────────────────────────────────────────
    hw_ok, hw_reason = _preflight_hardware(cache_root)
    if not hw_ok:
        return InstallOutcome(
            status="hardware_incompatible",
            note=(
                "hardware requirements not met — refusing to download a "
                "6+ GB model that cannot run on this machine. "
                f"{hw_reason}"
            ),
            exit_code=EXIT_HARDWARE_INCOMPATIBLE,
        )

    # ── Disk-space preflight ───────────────────────────────────────
    disk_ok, disk_reason, _free = _preflight_disk(cache_root, expected_bytes)
    if not disk_ok:
        return InstallOutcome(
            status="insufficient_disk",
            note=disk_reason,
            exit_code=EXIT_INSUFFICIENT_DISK,
        )

    # ── Acquire lock and perform the atomic install ────────────────
    try:
        with model_lock(lock_file, operation="install"):
            # If already installed AND verified, avoid re-downloading.
            if final_snap.is_dir():
                _emit("verifying_existing")
                already = full_verify_and_write_receipt(
                    manifest, manifest_path, cache_root,
                )
                if already.ok:
                    return InstallOutcome(
                        status="already_installed",
                        note=(
                            "model snapshot already present and verified; "
                            "no download attempted"
                        ),
                        exit_code=EXIT_OK,
                        snapshot_path=final_snap,
                        receipt_path=already.receipt_path,
                    )
                # Present but not verifying: refuse to overwrite in
                # place. Force the user through explicit remove.
                return InstallOutcome(
                    status="operation_failure",
                    note=(
                        f"snapshot present at {final_snap} but verification "
                        f"failed: {already.note} — run `aksharamd models "
                        "remove unlimited_ocr` and then install again"
                    ),
                    exit_code=EXIT_OPERATION_FAILURE,
                    snapshot_path=final_snap,
                )

            # Clean any leftover staging from a previously crashed run.
            _remove_tree_best_effort(staging)

            # Lazy import — keeps ``import aksharamd`` cheap.
            _emit("downloading")
            try:
                from huggingface_hub import snapshot_download  # type: ignore
            except ImportError as exc:
                return InstallOutcome(
                    status="operation_failure",
                    note=(
                        f"huggingface_hub is required to install this "
                        f"model but is not importable: {exc}"
                    ),
                    exit_code=EXIT_OPERATION_FAILURE,
                )

            allow_patterns = sorted(manifest["files"].keys())

            try:
                snapshot_download(
                    repo_id=repo_id,
                    revision=revision,
                    local_dir=str(staging),
                    allow_patterns=allow_patterns,
                    # trust_remote_code is NOT a snapshot_download
                    # parameter — kept explicit here so future edits
                    # do not slip it in.
                )
            except Exception as exc:
                _remove_tree_best_effort(staging)
                sanitized = _sanitize_error_message(f"{type(exc).__name__}: {exc}")
                return InstallOutcome(
                    status="download_failure",
                    note=f"snapshot download failed: {sanitized}",
                    exit_code=EXIT_DOWNLOAD_FAILURE,
                )

            # ── Byte verification against manifest ─────────────────
            _emit("verifying")
            # Temporarily move the staged snapshot into the final
            # location so verification_receipt (which resolves via
            # ``models--<slug>/snapshots/<revision>``) can find it.
            # This is safe: we still hold the lock, and on failure we
            # remove whatever is at final_snap.
            try:
                _promote_staging(staging, final_snap)
            except OSError as exc:
                _remove_tree_best_effort(staging)
                return InstallOutcome(
                    status="operation_failure",
                    note=f"promote from staging failed: {exc}",
                    exit_code=EXIT_OPERATION_FAILURE,
                )

            verify_out = full_verify_and_write_receipt(
                manifest, manifest_path, cache_root,
            )
            if not verify_out.ok:
                # Byte-mismatch → clean the promoted snapshot AND the
                # newly created receipt (invalidate is idempotent).
                _remove_tree_best_effort(final_snap)
                try:
                    invalidate_receipt(manifest, cache_root)
                except Exception:  # pragma: no cover - defensive
                    _logger.debug("invalidate_receipt after failure raised", exc_info=True)
                return InstallOutcome(
                    status="verification_failure",
                    note=f"byte verification failed: {verify_out.note}",
                    exit_code=EXIT_VERIFICATION_FAILURE,
                )

            _emit("done")
            return InstallOutcome(
                status="ok",
                note=(
                    f"installed {repo_id} @ {revision[:12]} to {final_snap} "
                    f"({len(verify_out.files_hashed)} files verified)"
                ),
                exit_code=EXIT_OK,
                snapshot_path=final_snap,
                receipt_path=verify_out.receipt_path,
                bytes_downloaded=expected_bytes,
            )
    except LockHeld as exc:
        holder = exc.holder or {}
        holder_str = (
            f"pid={holder.get('pid')} host={holder.get('hostname')} "
            f"op={holder.get('operation')} since={holder.get('created_at')}"
        ) if holder else "unknown holder"
        return InstallOutcome(
            status="operation_failure",
            note=(
                f"another aksharamd operation is already modifying this model "
                f"({holder_str}); wait for it to finish and retry"
            ),
            exit_code=EXIT_OPERATION_FAILURE,
        )


def _promote_staging(staging: Path, final: Path) -> None:
    """Atomically move the staging tree to ``final``.

    ``os.replace`` is atomic within a single volume but requires
    ``final`` to not already exist (POSIX allows overwriting an
    empty dir; Windows does not). We already refuse to overwrite an
    existing verified snapshot upstream, so any residue here is
    leftover garbage from a prior crash and can be removed.
    """
    if final.exists():
        _remove_tree_best_effort(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    # os.replace on a directory works on both POSIX and Windows
    # provided the destination does not exist.
    os.replace(str(staging), str(final))


# ── Verify ─────────────────────────────────────────────────────────────────


def verify_model() -> VerifyOutcome:
    """Byte-verify the installed snapshot. Network-free.

    Delegates entirely to
    :func:`~aksharamd.plugins.ocr_backends.verification_receipt.full_verify_and_write_receipt`.
    """
    try:
        manifest, manifest_path = _load_manifest()
    except Exception as exc:
        return VerifyOutcome(
            ok=False,
            note=f"cannot load trusted manifest: {exc}",
            exit_code=EXIT_OPERATION_FAILURE,
        )
    cache_root = _hf_cache_root()
    out = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    if not out.ok:
        return VerifyOutcome(
            ok=False,
            note=out.note,
            exit_code=EXIT_VERIFICATION_FAILURE,
            files_hashed=list(out.files_hashed),
        )
    return VerifyOutcome(
        ok=True,
        note=out.note,
        exit_code=EXIT_OK,
        receipt_path=out.receipt_path,
        files_hashed=list(out.files_hashed),
    )


# ── Remove ─────────────────────────────────────────────────────────────────


def _blob_targets_for_snapshot(snap: Path) -> tuple[list[Path], list[Path]]:
    """Return the list of blob paths (deduplicated) reachable from
    the snapshot's symlinks, and the list of snapshot entries that
    are symlinks.

    HuggingFace layout stores each file under ``blobs/<sha>`` and
    exposes it in ``snapshots/<revision>/<rel>`` as a symlink (or
    hardlink) into ``blobs/``. Removing the snapshot dir alone would
    leak the blob storage.

    On Windows, HuggingFace often stores plain files (copies) rather
    than symlinks — in that case there is no separate blob to remove
    and the list is empty.
    """
    blob_targets: set[Path] = set()
    symlinks: list[Path] = []
    if not snap.exists():
        return [], []
    for root, _dirs, files in os.walk(str(snap)):
        for f in files:
            p = Path(root) / f
            try:
                if p.is_symlink():
                    symlinks.append(p)
                    tgt = Path(os.readlink(str(p)))
                    if not tgt.is_absolute():
                        tgt = (p.parent / tgt).resolve(strict=False)
                    else:
                        tgt = tgt.resolve(strict=False)
                    blob_targets.add(tgt)
            except OSError:
                continue
    return sorted(blob_targets), symlinks


def _snapshot_size_bytes(snap: Path) -> int:
    """Sum of file sizes reachable through the snapshot (following
    symlinks so blob contents count once)."""
    seen: set[Path] = set()
    total = 0
    if not snap.exists():
        return 0
    for root, _dirs, files in os.walk(str(snap)):
        for f in files:
            p = Path(root) / f
            try:
                real = p.resolve(strict=False)
                if real in seen:
                    continue
                seen.add(real)
                if real.exists():
                    total += real.stat().st_size
            except OSError:
                continue
    return total


def remove_model(
    *,
    clear_runtime_cache: bool = False,
) -> RemoveOutcome:
    """Remove the Unlimited-OCR model snapshot from the local cache.

    Only the pinned snapshot dir and its blobs are removed. Other
    snapshots of the same repo (if a user has multiple revisions
    cached) remain untouched. Idempotent: running twice returns
    ``already_absent`` on the second call.

    ``clear_runtime_cache`` additionally removes the small
    aksharamd-managed runtime cache (safe-size cache under
    ``~/.aksharamd/``). Never removes anything else.
    """
    try:
        repo_id, revision = _load_pinned_reference()
    except Exception as exc:
        return RemoveOutcome(
            status="operation_failure",
            note=f"cannot resolve pinned model reference: {exc}",
            exit_code=EXIT_OPERATION_FAILURE,
        )

    cache_root = _hf_cache_root()
    snap = _snapshot_dir(cache_root, repo_id, revision)
    lock_file = _lock_path(cache_root, repo_id)

    # Even the removal check acquires the lock — protects against a
    # concurrent install racing us.
    try:
        with model_lock(lock_file, operation="remove"):
            if not snap.exists():
                # Optionally clear the runtime cache too. This is not
                # what the user asked for if snapshot was absent, but
                # it is still safe to do — record the fact.
                runtime_cleared = False
                if clear_runtime_cache:
                    runtime_cleared = _clear_runtime_cache_best_effort()
                return RemoveOutcome(
                    status="already_absent",
                    note=(
                        "no snapshot present at the pinned revision; "
                        "nothing to remove"
                    ),
                    exit_code=EXIT_OK,
                    runtime_cache_cleared=runtime_cleared,
                )

            bytes_before = _snapshot_size_bytes(snap)
            blob_targets, _symlinks = _blob_targets_for_snapshot(snap)

            # Remove snapshot dir first.
            _remove_tree_best_effort(snap)
            # Then remove blob targets that lived inside the same
            # models--<slug> tree (never anything outside).
            repo_tree = _repo_root(cache_root, repo_id).resolve(strict=False)
            blobs_removed = 0
            for tgt in blob_targets:
                try:
                    tgt_resolved = tgt.resolve(strict=False)
                except OSError:
                    continue
                # Safety: only touch files that live under this repo's
                # cache tree. Never chase a symlink out of the repo.
                try:
                    tgt_resolved.relative_to(repo_tree)
                except ValueError:
                    continue
                try:
                    if tgt_resolved.is_file():
                        tgt_resolved.unlink()
                        blobs_removed += 1
                except OSError as exc:
                    _logger.debug(
                        "could not remove blob %s: %s", tgt_resolved, exc,
                    )

            # Invalidate the aksharamd receipt for this manifest —
            # keeps the receipt-store clean.
            try:
                manifest, _ = _load_manifest()
                invalidate_receipt(manifest, cache_root)
            except Exception as exc:
                _logger.debug("invalidate_receipt during remove failed: %s", exc)

            runtime_cleared = False
            if clear_runtime_cache:
                runtime_cleared = _clear_runtime_cache_best_effort()

            return RemoveOutcome(
                status="ok",
                note=(
                    f"removed snapshot at {snap} and {blobs_removed} blob(s); "
                    f"recovered {bytes_before // _MIB} MiB"
                ),
                exit_code=EXIT_OK,
                bytes_recovered=bytes_before,
                snapshot_removed=True,
                blobs_removed=blobs_removed,
                runtime_cache_cleared=runtime_cleared,
            )
    except LockHeld as exc:
        holder = exc.holder or {}
        holder_str = (
            f"pid={holder.get('pid')} host={holder.get('hostname')} "
            f"op={holder.get('operation')} since={holder.get('created_at')}"
        ) if holder else "unknown holder"
        return RemoveOutcome(
            status="operation_failure",
            note=(
                f"another aksharamd operation is already modifying this model "
                f"({holder_str}); wait for it to finish and retry"
            ),
            exit_code=EXIT_OPERATION_FAILURE,
        )


def _clear_runtime_cache_best_effort() -> bool:
    """Remove the small aksharamd-managed safe-size cache. Returns
    True on success (or if nothing was there); False if the removal
    failed but the operation should not itself fail.
    """
    try:
        from .cache import cache_dir  # type: ignore[attr-defined]
        root = Path(cache_dir())
    except Exception:
        root = Path.home() / ".aksharamd" / "unlimited_ocr_cache"
    try:
        if root.exists():
            shutil.rmtree(str(root), ignore_errors=True)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("could not clear runtime cache at %s: %s", root, exc)
        return False


# ── Helper: JSON-serializable projection ───────────────────────────────────


def status_to_dict(status: ModelStatus) -> dict[str, Any]:
    """Return a JSON-friendly representation of a :class:`ModelStatus`."""
    return {
        "name": status.name,
        "repo_id": status.repo_id,
        "revision": status.revision,
        "download_size_bytes": status.download_size_bytes,
        "download_size_source": status.download_size_source,
        "snapshot_present": status.snapshot_present,
        "manifest_present": status.manifest_present,
        "byte_verified": status.byte_verified,
        "hardware_compatible": status.hardware_compatible,
        "runnable_now": status.runnable_now,
        "snapshot_path": str(status.snapshot_path) if status.snapshot_path else None,
        "receipt_path": str(status.receipt_path) if status.receipt_path else None,
        "reason": status.reason,
        "availability_details": status.availability_details,
    }


def verify_outcome_to_dict(out: VerifyOutcome) -> dict[str, Any]:
    """Return a JSON-friendly representation of a :class:`VerifyOutcome`."""
    return {
        "ok": out.ok,
        "note": out.note,
        "exit_code": out.exit_code,
        "receipt_path": str(out.receipt_path) if out.receipt_path else None,
        "files_hashed": list(out.files_hashed),
    }


# Suppress "unused import" warnings for symbols we re-export.
__all__ = [
    "DownloadSizeSource",
    "EXIT_DOWNLOAD_FAILURE",
    "EXIT_HARDWARE_INCOMPATIBLE",
    "EXIT_INSUFFICIENT_DISK",
    "EXIT_INVALID_COMMAND",
    "EXIT_OK",
    "EXIT_OPERATION_FAILURE",
    "EXIT_VERIFICATION_FAILURE",
    "InstallOutcome",
    "LockHeld",
    "MODEL_NAME",
    "ModelInfo",
    "ModelStatus",
    "RemoveOutcome",
    "VerifyOutcome",
    "get_model_info",
    "get_model_status",
    "install_model",
    "remove_model",
    "status_to_dict",
    "verify_model",
    "verify_outcome_to_dict",
]

