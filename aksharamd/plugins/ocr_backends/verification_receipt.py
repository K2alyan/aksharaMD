"""Verification receipt for the Unlimited-OCR trusted snapshot (A1b).

The 6.67 GB safetensors shard makes SHA-256 verification at every model
load impractical (~15 s wall time on SATA SSD). This module provides
two verification modes:

- **Full mode** — hashes every file in the runtime manifest (including
  the safetensors shard), writes a signed-by-integrity-check receipt.
  Called after ``aksharamd models install`` and on explicit
  ``aksharamd models verify``.

- **Fast mode** — hashes only the small, security-sensitive runtime
  files on every model load (11 files: 5 executable Python modules +
  6 configuration-sensitive JSON files, ~10 MB total, sub-second).
  For the safetensors shard, validates a previously written receipt
  by comparing recorded size + mtime + stable file identity.

## Tamper limits (must be understood before use)

The receipt file is a **plaintext JSON on the user's filesystem** with
no cryptographic signature. It protects against:

- Ordinary tampering that changes size, mtime, or file identity.
- Cache-directory replacement by an out-of-band process (e.g., someone
  swaps the weights file between benchmark runs).
- Stale verification (revision changed, manifest changed,
  verification-implementation-version incremented → receipt discarded).

The receipt **does NOT protect against** a local attacker who can:

- Modify both the model cache AND the receipt directory (they can
  swap in tampered weights and write a matching receipt).
- Override the ``aksharamd`` code path itself.

Fast mode is **not equivalent** to rehashing. It is a bounded-cost
check for the common case (files unchanged since last full verify).
Users who need cryptographic-authenticity guarantees should run full
mode (``aksharamd models verify``) on every restart.

The receipt is written with **user-only permissions** where the platform
supports it (POSIX: ``0o600``; on Windows the file inherits directory
ACLs — no additional hardening applied).

## Coupling policy

The receipt schema and validity are deliberately **decoupled** from
``SCORING_POLICY_VERSION``. Bumping the readiness-scoring rules does
not invalidate an integrity receipt. The dedicated
``VERIFICATION_IMPLEMENTATION_VERSION`` constant below tracks
security-sensitive changes to verification behavior. Increment it when:

- A new invalidation condition is added.
- Symlink policy or file-set classification changes materially.
- Hash algorithm changes.

Do not increment it for cosmetic changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Increment when security-sensitive verification behavior changes.
# Any bump invalidates every existing receipt for every manifest.
VERIFICATION_IMPLEMENTATION_VERSION = 1

# Increment only when the receipt JSON shape changes in a way that
# older code cannot read.
RECEIPT_SCHEMA_VERSION = 1


class ReceiptError(Exception):
    """Raised on receipt read/write/validation failure."""


@dataclass(frozen=True)
class ReceiptEntry:
    """Per-file entry inside a receipt."""
    path: str
    expected_sha256: str
    actual_sha256: str
    size_bytes: int
    mtime_ns: int
    file_identity: str  # inode or platform-stable id; empty if unavailable


@dataclass
class VerificationOutcome:
    """Return value from full/fast verify."""
    ok: bool
    note: str
    receipt_path: Path | None = None
    files_hashed: list[str] = field(default_factory=list)
    files_checked_by_receipt: list[str] = field(default_factory=list)


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_identity(p: Path) -> str:
    """Return a stable per-file identifier if the platform supports it,
    otherwise an empty string. Used to detect same-size-same-mtime
    replacement.
    """
    try:
        st = p.stat()
    except OSError:
        return ""
    ino = getattr(st, "st_ino", 0)
    if ino:
        return f"ino:{ino}"
    return ""


def _manifest_sha256(manifest_path: Path) -> str:
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _receipt_dir(cache_root: Path) -> Path:
    """Directory where AksharaMD writes its verification receipts.
    Held under HF_HOME so removal of the model cache also removes the
    receipts.
    """
    return cache_root / "aksharamd_verification_receipts"


def receipt_path(manifest: dict, cache_root: Path | None = None) -> Path:
    """Compute the receipt path for the given manifest.
    Includes ``manifest_id`` in the filename so multiple manifests
    coexist without collision.
    """
    if cache_root is None:
        cache_root = Path(
            os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface" / "hub"
        )
    repo_slug = manifest["repo_id"].replace("/", "--")
    mid = manifest["manifest_id"]
    return _receipt_dir(cache_root) / f"{repo_slug}__{mid}.json"


def _write_atomic_user_only(path: Path, payload: dict) -> None:
    """Atomic write with user-only permissions where the platform supports it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    data = text.encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # POSIX-only: restrict to 0600 before promoting.
        if platform.system() != "Windows":
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_name, path)
    except Exception:
        # Ensure the temp file is not left behind on failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _resolve_snapshot(manifest: dict, cache_root: Path | None) -> Path:
    """Find the snapshot directory for the manifest's pinned revision.
    Raises ``ReceiptError`` if not present.
    """
    if cache_root is None:
        cache_root = Path(
            os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface" / "hub"
        )
    repo_slug = manifest["repo_id"].replace("/", "--")
    snap = cache_root / f"models--{repo_slug}" / "snapshots" / manifest["revision"]
    if not snap.is_dir():
        raise ReceiptError(f"snapshot not present at pinned revision: {snap}")
    return snap


def _hash_files(
    manifest: dict,
    snap: Path,
    *,
    include_weights: bool,
) -> tuple[list[ReceiptEntry], str | None]:
    """Hash the runtime files. Returns (entries, error_note).
    On any mismatch the error_note is set and hashing stops early.
    """
    entries: list[ReceiptEntry] = []
    for rel, meta in manifest["files"].items():
        should_hash = bool(meta.get("verify_on_every_load", False)) or (
            include_weights and meta.get("class") == "weights"
        )
        if not should_hash:
            continue
        p = snap / rel
        if not p.exists():
            return entries, f"manifest file missing from snapshot: {rel}"
        actual = _sha256_file(p)
        if actual != meta["sha256"]:
            return entries, (
                f"SHA-256 mismatch on {rel}: expected {meta['sha256'][:12]}..., "
                f"got {actual[:12]}..."
            )
        entries.append(ReceiptEntry(
            path=rel,
            expected_sha256=meta["sha256"],
            actual_sha256=actual,
            size_bytes=p.stat().st_size,
            mtime_ns=p.stat().st_mtime_ns,
            file_identity=_file_identity(p),
        ))
    return entries, None


def full_verify_and_write_receipt(
    manifest: dict,
    manifest_path: Path,
    cache_root: Path | None = None,
) -> VerificationOutcome:
    """Full mode: hash every runtime file (executables + configs +
    weights), then write a receipt.

    Call after ``aksharamd models install`` and from
    ``aksharamd models verify``.
    """
    try:
        snap = _resolve_snapshot(manifest, cache_root)
    except ReceiptError as e:
        return VerificationOutcome(ok=False, note=str(e))
    entries, err = _hash_files(manifest, snap, include_weights=True)
    if err is not None:
        return VerificationOutcome(ok=False, note=err, files_hashed=[e.path for e in entries])
    rpath = receipt_path(manifest, cache_root)
    payload = {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "verification_implementation_version": VERIFICATION_IMPLEMENTATION_VERSION,
        "manifest_id": manifest["manifest_id"],
        "manifest_schema_version": manifest["manifest_schema_version"],
        "manifest_sha256": _manifest_sha256(manifest_path),
        "repo_id": manifest["repo_id"],
        "revision": manifest["revision"],
        "snapshot_path": str(snap.resolve()),
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verified_by": {
            "hostname_hash": hashlib.sha256(platform.node().encode()).hexdigest()[:16],
            "os": platform.system(),
        },
        "entries": [asdict(e) for e in entries],
    }
    try:
        _write_atomic_user_only(rpath, payload)
    except OSError as e:
        return VerificationOutcome(
            ok=False,
            note=f"receipt write failed: {e}",
            files_hashed=[e.path for e in entries],
        )
    return VerificationOutcome(
        ok=True,
        note=f"full verify passed; receipt at {rpath}",
        receipt_path=rpath,
        files_hashed=[e.path for e in entries],
    )


def _load_receipt(rpath: Path) -> dict[str, Any]:
    try:
        return json.loads(rpath.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ReceiptError(f"receipt missing: {rpath}") from e
    except json.JSONDecodeError as e:
        raise ReceiptError(f"receipt not valid JSON: {e}") from e


def _receipt_matches_manifest(
    receipt: dict,
    manifest: dict,
    manifest_path: Path,
) -> tuple[bool, str]:
    """Cheap invalidation checks. Ordered so the most-common changes
    are caught first."""
    if receipt.get("receipt_schema_version") != RECEIPT_SCHEMA_VERSION:
        return False, "receipt_schema_version changed"
    if receipt.get("verification_implementation_version") != VERIFICATION_IMPLEMENTATION_VERSION:
        return False, "verification_implementation_version changed"
    if receipt.get("manifest_id") != manifest["manifest_id"]:
        return False, "manifest_id changed"
    if receipt.get("manifest_schema_version") != manifest["manifest_schema_version"]:
        return False, "manifest_schema_version changed"
    if receipt.get("revision") != manifest["revision"]:
        return False, "revision changed"
    if receipt.get("repo_id") != manifest["repo_id"]:
        return False, "repo_id changed"
    if receipt.get("manifest_sha256") != _manifest_sha256(manifest_path):
        return False, "manifest_sha256 changed (manifest file bytes differ)"
    return True, ""


def fast_verify(
    manifest: dict,
    manifest_path: Path,
    cache_root: Path | None = None,
) -> VerificationOutcome:
    """Fast mode: hash the small security-sensitive files on every call
    (executables + configs — 11 files, ~10 MB). For weights, validate
    the previously written receipt's size + mtime + file identity.

    Refuses if the receipt is missing, does not match the manifest,
    or if any recorded weights file's stat drifts from the receipt.
    Caller must run ``full_verify_and_write_receipt`` to recover.
    """
    try:
        snap = _resolve_snapshot(manifest, cache_root)
    except ReceiptError as e:
        return VerificationOutcome(ok=False, note=str(e))

    # Step 1: hash the 11 small files fresh.
    entries, err = _hash_files(manifest, snap, include_weights=False)
    if err is not None:
        return VerificationOutcome(
            ok=False, note=err, files_hashed=[e.path for e in entries]
        )
    hashed_paths = [e.path for e in entries]

    # Step 2: check the receipt covers weights + everything unchanged.
    rpath = receipt_path(manifest, cache_root)
    try:
        receipt = _load_receipt(rpath)
    except ReceiptError as e:
        return VerificationOutcome(
            ok=False,
            note=f"{e} — run `aksharamd models verify` (full mode) to recover",
            files_hashed=hashed_paths,
        )
    ok, note = _receipt_matches_manifest(receipt, manifest, manifest_path)
    if not ok:
        return VerificationOutcome(
            ok=False,
            note=f"receipt invalid: {note} — run `aksharamd models verify` to recover",
            files_hashed=hashed_paths,
        )

    # Step 3: cross-check weights entries in the receipt against current stat.
    receipt_entries = {e["path"]: e for e in receipt.get("entries", [])}
    checked: list[str] = []
    for rel, meta in manifest["files"].items():
        if meta.get("class") != "weights":
            continue
        if rel not in receipt_entries:
            return VerificationOutcome(
                ok=False,
                note=f"receipt does not cover weights file {rel}; run full verify",
                files_hashed=hashed_paths,
                files_checked_by_receipt=checked,
            )
        rec = receipt_entries[rel]
        p = snap / rel
        if not p.exists():
            return VerificationOutcome(
                ok=False,
                note=f"weights file missing from snapshot: {rel}",
                files_hashed=hashed_paths,
                files_checked_by_receipt=checked,
            )
        st = p.stat()
        if st.st_size != rec["size_bytes"]:
            return VerificationOutcome(
                ok=False,
                note=f"weights size drifted for {rel}; run full verify",
                files_hashed=hashed_paths,
                files_checked_by_receipt=checked,
            )
        if st.st_mtime_ns != rec["mtime_ns"]:
            return VerificationOutcome(
                ok=False,
                note=f"weights mtime drifted for {rel}; run full verify",
                files_hashed=hashed_paths,
                files_checked_by_receipt=checked,
            )
        current_id = _file_identity(p)
        recorded_id = rec.get("file_identity", "")
        # Only enforce identity if both sides recorded one. If either is
        # empty (e.g., different filesystem), skip this check and rely on
        # size+mtime alone.
        if current_id and recorded_id and current_id != recorded_id:
            return VerificationOutcome(
                ok=False,
                note=f"weights file identity drifted for {rel}; run full verify",
                files_hashed=hashed_paths,
                files_checked_by_receipt=checked,
            )
        checked.append(rel)

    return VerificationOutcome(
        ok=True,
        note=f"fast verify passed ({len(hashed_paths)} hashed, {len(checked)} covered by receipt)",
        receipt_path=rpath,
        files_hashed=hashed_paths,
        files_checked_by_receipt=checked,
    )


def invalidate_receipt(manifest: dict, cache_root: Path | None = None) -> bool:
    """Delete the receipt for the given manifest. Returns True if the
    receipt existed and was removed. Idempotent."""
    rpath = receipt_path(manifest, cache_root)
    try:
        rpath.unlink()
        return True
    except FileNotFoundError:
        return False
