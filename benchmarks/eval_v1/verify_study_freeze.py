"""CI verification for Study Freeze Manifest V1.

Checks that implementation/configuration references in the manifest
resolve to the expected versions in the codebase. Fails with a non-zero
exit code if any reference is wrong or missing.

Run:
    python benchmarks/eval_v1/verify_study_freeze.py
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MANIFEST = ROOT / "docs" / "evaluation" / "STUDY_FREEZE_MANIFEST_V1.md"

FAILURES: list[str] = []


def _fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"  FAIL: {msg}")


def _ok(msg: str) -> None:
    print(f"  ok  : {msg}")


# ---------------------------------------------------------------------------
# 1. Manifest exists and is the authorized version.
print("=== 1) manifest present and authorized ===")
if not MANIFEST.exists():
    _fail(f"manifest not found: {MANIFEST}")
    sys.exit(1)

text = MANIFEST.read_text(encoding="utf-8")

if "AUTHORIZED — Study Freeze V1" not in text:
    _fail("manifest status is not AUTHORIZED — Study Freeze V1")
else:
    _ok("manifest status = AUTHORIZED")

# ---------------------------------------------------------------------------
# 2. SCORING_POLICY_VERSION matches codebase.
print()
print("=== 2) SCORING_POLICY_VERSION ===")
FROZEN_SPV = "1.10"

models_py = ROOT / "aksharamd" / "scoring" / "models.py"
if not models_py.exists():
    _fail("aksharamd/scoring/models.py not found")
else:
    m = re.search(r'^SCORING_POLICY_VERSION\s*=\s*"([^"]+)"', models_py.read_text(), re.MULTILINE)
    if not m:
        _fail("SCORING_POLICY_VERSION not found in aksharamd/scoring/models.py")
    elif m.group(1) != FROZEN_SPV:
        _fail(
            f"SCORING_POLICY_VERSION in codebase is {m.group(1)!r}, "
            f"manifest freezes {FROZEN_SPV!r} — scoring policy has drifted"
        )
    else:
        _ok(f"SCORING_POLICY_VERSION = {FROZEN_SPV!r} matches codebase")

if f'"{FROZEN_SPV}"' not in text:
    _fail(f"manifest does not reference SCORING_POLICY_VERSION {FROZEN_SPV!r}")
else:
    _ok(f"manifest references SCORING_POLICY_VERSION {FROZEN_SPV!r}")

# ---------------------------------------------------------------------------
# 3. Frozen detector IDs exist in readiness.py.
print()
print("=== 3) detector IDs in readiness.py ===")
FROZEN_DETECTORS = [
    "W_MULTICOLUMN_ORDER",
    "W_TABLE_MISSING",
    "W_ENCODING_ARTIFACTS",
    "W_IMAGE_ONLY_TEXT_BAR_FAIL",
    "W_DROPPED_CONTENT",
    "W_HEADER_FOOTER_TABLE_GARBLED",
    "W_TABLE_EXPECTED_NOT_EXTRACTED",
    "W_PLACEHOLDER_STUB",
    "W_GIBBERISH",
]

readiness_py = ROOT / "aksharamd" / "scoring" / "readiness.py"
if not readiness_py.exists():
    _fail("aksharamd/scoring/readiness.py not found")
else:
    readiness_text = readiness_py.read_text(encoding="utf-8")
    for det in FROZEN_DETECTORS:
        if f'"{det}"' in readiness_text or f"'{det}'" in readiness_text:
            _ok(f"detector {det} present in readiness.py")
        else:
            _fail(f"detector {det} NOT found in readiness.py — detector may have been renamed or removed")

# ---------------------------------------------------------------------------
# 4. Frozen parser IDs known to the smoke apparatus.
print()
print("=== 4) parser IDs in smoke apparatus ===")
FROZEN_PARSERS = ["aksharamd-reference", "marker", "docling", "markitdown"]

workers_main = ROOT / "benchmarks" / "eval_v1" / "smoke_b1a_7b" / "workers" / "main.py"
if not workers_main.exists():
    _fail(f"workers/main.py not found at {workers_main}")
else:
    workers_text = workers_main.read_text(encoding="utf-8")
    for pid in FROZEN_PARSERS:
        if pid in workers_text:
            _ok(f"parser {pid!r} present in workers/main.py")
        else:
            _fail(f"parser {pid!r} NOT found in workers/main.py")

# ---------------------------------------------------------------------------
# 5. Freeze seed present in manifest.
print()
print("=== 5) freeze seed in manifest ===")
FROZEN_SEED = "6c270ac293b348ca27279bdd012375aa6c707be494085e70781637a99a6322fa"
if FROZEN_SEED in text:
    _ok(f"freeze seed present: {FROZEN_SEED[:16]}…")
else:
    _fail("freeze seed not found in manifest")

# ---------------------------------------------------------------------------
# 6. Manifest raw-byte SHA-256 (informational — printed for PR record).
print()
print("=== 6) manifest content hashes ===")
raw_bytes = MANIFEST.read_bytes()
raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
canonical = "\n".join(text.splitlines()) + "\n"
canonical_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
print(f"  raw-byte SHA-256       : {raw_sha256}")
print(f"  canonical (LF) SHA-256 : {canonical_sha256}")
print(f"  size (bytes)           : {len(raw_bytes)}")

# ---------------------------------------------------------------------------
# Summary.
print()
print("=" * 60)
if not FAILURES:
    print("STUDY FREEZE VERIFICATION: ALL CHECKS PASSED")
    sys.exit(0)
else:
    print(f"STUDY FREEZE VERIFICATION: {len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
