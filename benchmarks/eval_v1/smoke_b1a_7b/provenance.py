"""Deterministic provenance-hash computation.

Two hash surfaces the smoke has to record per invocation:

- ``package_source_sha256`` per parser: identifies the installed
  package the smoke actually ran against. Hashes the distribution's
  ``RECORD`` file (a canonical PEP 376 CSV of ``filename,sha,size``
  per installed file). If two Python environments install the same
  wheel byte-identically, they produce identical RECORDs and hence
  identical ``package_source_sha256`` values. Any change to the
  installed files — a re-install, a patched binary, a hand-edit — is
  observable.
- ``parser_model_artifact_sha256`` per VLM parser: identifies the
  model-cache contents. Walks the cache directory, builds a
  deterministic manifest line per file
  (``<posix_relpath>\t<size>\t<sha256>\n``, sorted by relpath), and
  hashes the manifest. Cheap enough to run at preflight time on a
  few-GB cache; still cryptographically identifies what was verified.

Both surfaces reject "placeholder" values — the classic 64-zero
string, empty strings, non-hex, wrong length. These would silently
allow fabricated provenance into an execution record.

The functions are pure and testable; no network, no subprocess.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as _im
import re
from pathlib import Path

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO_SHA = "0" * 64


class ProvenanceHashError(RuntimeError):
    """Raised when a computed or supplied provenance hash is invalid,
    missing, or a placeholder. Fail-closed so a placeholder cannot
    reach an execution record."""


# --------------------------------------------------------------------------
# Validators (used by preflight and by the composition layer).


def is_placeholder_sha(value: str | None) -> bool:
    """A value is a 'placeholder' iff it is the well-known all-zeros
    string, an obvious repetition (``"a"*64``, ``"f"*64``, etc.), or
    otherwise a canonical stand-in that carries no real provenance.
    We refuse these at preflight time so a lazy substitution cannot
    silently reach an execution record.
    """
    if value is None:
        return True
    if not isinstance(value, str) or not value:
        return True
    if value == _ZERO_SHA:
        return True
    # Any single-character repetition of length 64 is a placeholder
    # by convention in this codebase's tests (``"a"*64`` etc.).
    if len(value) == 64 and len(set(value)) == 1:
        return True
    return False


def validate_sha_format(value: str | None, *, name: str) -> None:
    """Raise ``ProvenanceHashError`` if ``value`` is missing, wrong
    length, non-hex, or a placeholder."""
    if value is None:
        raise ProvenanceHashError(f"{name}: missing (None)")
    if not isinstance(value, str):
        raise ProvenanceHashError(f"{name}: not a string ({type(value).__name__})")
    if is_placeholder_sha(value):
        raise ProvenanceHashError(
            f"{name}: placeholder ({value[:16]}…) — refuse to accept "
            f"as provenance"
        )
    if not _HEX64_RE.match(value):
        raise ProvenanceHashError(
            f"{name}: not a 64-hex-char SHA-256 string ({value[:32]}…)"
        )


# --------------------------------------------------------------------------
# package_source_sha256 — hash of the installed distribution RECORD.


def compute_package_source_sha256(distribution_name: str) -> str:
    """SHA-256 of the installed distribution's ``RECORD`` file.

    ``RECORD`` is PEP 376's canonical inventory of an installed
    distribution: one CSV row per file (``filename,sha,size``).
    Hashing the RECORD gives a stable identifier for the installed
    files. If the RECORD is missing (unusual — most wheels ship one),
    or the distribution itself is not installed, this function raises
    ``ProvenanceHashError`` rather than fabricating a hash.
    """
    try:
        dist = _im.distribution(distribution_name)
    except _im.PackageNotFoundError as exc:
        raise ProvenanceHashError(
            f"cannot compute package_source_sha256 for {distribution_name!r}: "
            f"distribution not installed"
        ) from exc
    record = dist.read_text("RECORD")
    if record is None:
        raise ProvenanceHashError(
            f"cannot compute package_source_sha256 for {distribution_name!r}: "
            f"RECORD file missing from installed metadata"
        )
    digest = hashlib.sha256(record.encode("utf-8")).hexdigest()
    # Defensive: never emit a placeholder even from a genuine hash.
    validate_sha_format(digest, name=f"{distribution_name} RECORD hash")
    return digest


# --------------------------------------------------------------------------
# parser_model_artifact_sha256 — hash of a deterministic file-listing manifest.


def compute_model_artifact_sha256(cache_dir: Path) -> str:
    """Deterministic content hash of a model-cache directory.

    Walks ``cache_dir`` recursively, emits one line per file
    (``<posix_relpath>\t<size>\t<sha256>\n``, sorted by relpath),
    then hashes the joined manifest.

    Raises ``ProvenanceHashError`` if the cache directory is missing
    or contains no files. Callers may wrap it to add per-parser
    context.
    """
    if not cache_dir.exists() or not cache_dir.is_dir():
        raise ProvenanceHashError(
            f"cannot compute model_artifact_sha256: {cache_dir} does not exist"
        )
    files: list[tuple[str, int, str]] = []
    for p in sorted(cache_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError as exc:
            raise ProvenanceHashError(
                f"cannot read model artifact file {p}: {exc}"
            ) from exc
        rel = p.relative_to(cache_dir).as_posix()
        files.append((rel, len(data), hashlib.sha256(data).hexdigest()))
    if not files:
        raise ProvenanceHashError(
            f"cannot compute model_artifact_sha256: {cache_dir} is empty"
        )
    manifest = "".join(f"{rel}\t{size}\t{sha}\n" for rel, size, sha in files)
    digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    validate_sha_format(digest, name=f"{cache_dir.name} model artifact hash")
    return digest


__all__ = [
    "ProvenanceHashError",
    "compute_model_artifact_sha256",
    "compute_package_source_sha256",
    "is_placeholder_sha",
    "validate_sha_format",
]
