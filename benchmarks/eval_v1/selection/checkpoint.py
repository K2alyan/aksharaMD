"""Phase-level immutable population checkpoints.

Turns caching into reproducible-computation memoization: a checkpoint
is reusable only if every fingerprint (algorithm version, source
snapshot identity, eligibility-rule version, exclusion-ledger hash,
checkpoint schema version, payload hash) matches. Otherwise the phase
is recomputed. Failure is always fail-closed — a mismatched checkpoint
never silently degrades to "reuse anyway."

Checkpoints are written after each phase succeeds AND before the next
phase begins, so a mid-run failure never loses more than the current
phase's work.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHECKPOINT_SCHEMA_VERSION = "1"


class CheckpointMismatchError(RuntimeError):
    """Raised when a persisted checkpoint's fingerprints disagree with
    what the current run would compute. The phase MUST be recomputed;
    the stale checkpoint MUST NOT be silently reused."""


@dataclass(frozen=True)
class CheckpointFingerprint:
    """Every field must match for a checkpoint to be reusable.

    ``payload_sha256`` is computed over the JSON-serialized payload
    (with sorted keys) so the payload itself is self-verifying.
    """

    corpus: str
    selection_algorithm_version: str
    source_snapshot_identity_sha256: str
    eligibility_rule_version: str
    exclusion_ledger_sha256: str
    checkpoint_schema_version: str = CHECKPOINT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, str]:
        return {
            "corpus": self.corpus,
            "selection_algorithm_version": self.selection_algorithm_version,
            "source_snapshot_identity_sha256": self.source_snapshot_identity_sha256,
            "eligibility_rule_version": self.eligibility_rule_version,
            "exclusion_ledger_sha256": self.exclusion_ledger_sha256,
            "checkpoint_schema_version": self.checkpoint_schema_version,
        }


def sha256_json(obj: Any) -> str:
    """Hash a JSON-serializable object under canonical formatting."""
    b = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    if not path.exists():
        return hashlib.sha256(b"").hexdigest()
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checkpoint(
    path: Path,
    fingerprint: CheckpointFingerprint,
    payload: dict[str, Any],
) -> None:
    """Persist a checkpoint atomically.

    Layout:
      { "fingerprint": {...}, "payload": {...}, "payload_sha256": "..." }
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "fingerprint": fingerprint.as_dict(),
        "payload": payload,
        "payload_sha256": sha256_json(payload),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True))
    tmp.replace(path)


def load_checkpoint(
    path: Path,
    expected: CheckpointFingerprint,
) -> dict[str, Any]:
    """Return payload if every fingerprint field matches; else raise.

    Fail-closed: any mismatch means the caller MUST recompute the phase,
    never fall back to the stale checkpoint.
    """
    if not path.exists():
        raise CheckpointMismatchError(f"no checkpoint at {path}")
    body = json.loads(path.read_text())
    stored = body.get("fingerprint", {})
    for k, v in expected.as_dict().items():
        if stored.get(k) != v:
            raise CheckpointMismatchError(
                f"{path}: fingerprint mismatch on {k!r}: "
                f"stored={stored.get(k)!r} expected={v!r}"
            )
    payload = body.get("payload", {})
    if sha256_json(payload) != body.get("payload_sha256"):
        raise CheckpointMismatchError(
            f"{path}: payload SHA-256 does not match recorded hash — "
            f"checkpoint tampered or truncated"
        )
    return payload


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointFingerprint",
    "CheckpointMismatchError",
    "load_checkpoint",
    "sha256_file",
    "sha256_json",
    "write_checkpoint",
]
