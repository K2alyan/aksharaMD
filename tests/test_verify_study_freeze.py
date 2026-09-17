"""Regression tests for verify_study_freeze.py §7 — Track C prompt SHA check.

Guards against silent drift of TRACK_C_PROMPT_V1.txt relative to the SHA-256
pin in STUDY_FREEZE_MANIFEST_V1.md.  Discovered via B1a-8d: the original §8
pin was erroneous and was not caught until execution-manifest construction.

Convention (frozen by B1a-8d / §15 of STUDY_FREEZE_MANIFEST_V1.md):
    SHA-256 of LF-normalized content (all CRLF and lone CR → LF).
    This equals the git blob SHA and is stable across platforms regardless of
    host core.autocrlf setting.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROMPT_PATH = ROOT / "docs" / "evaluation" / "TRACK_C_PROMPT_V1.txt"
MANIFEST_PATH = ROOT / "docs" / "evaluation" / "STUDY_FREEZE_MANIFEST_V1.md"

FROZEN_PROMPT_SHA256 = (
    "2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601"
)


def _canonical_lf_sha256(path: Path) -> str:
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(lf).hexdigest()


# ---------------------------------------------------------------------------
# §7 — prompt file integrity

def test_prompt_file_exists() -> None:
    assert PROMPT_PATH.exists(), f"TRACK_C_PROMPT_V1.txt not found at {PROMPT_PATH}"


def test_prompt_canonical_lf_sha256_matches_frozen_pin() -> None:
    """Canonical-LF SHA-256 of the prompt must match the §8 pin.

    Canonical-LF means: normalize all CRLF and lone CR to LF before hashing.
    This equals the git blob SHA and is stable across Windows (CRLF checkout
    with core.autocrlf=true) and Linux (LF checkout).
    """
    actual = _canonical_lf_sha256(PROMPT_PATH)
    assert actual == FROZEN_PROMPT_SHA256, (
        f"TRACK_C_PROMPT_V1.txt canonical-LF SHA-256 mismatch.\n"
        f"  frozen : {FROZEN_PROMPT_SHA256}\n"
        f"  actual : {actual}\n"
        f"If the prompt content was intentionally changed, update the pin in "
        f"verify_study_freeze.py AND add an amendment to §15 of "
        f"STUDY_FREEZE_MANIFEST_V1.md before any Track C execution."
    )


def test_frozen_pin_present_in_manifest() -> None:
    """The corrected SHA must appear in the freeze manifest (§8 and §13)."""
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    count = manifest_text.count(FROZEN_PROMPT_SHA256)
    assert count >= 2, (
        f"Expected frozen prompt SHA to appear at least twice in the manifest "
        f"(§8 and §13), found {count} occurrence(s).  "
        f"SHA: {FROZEN_PROMPT_SHA256}"
    )


def test_erroneous_sha_not_in_section8() -> None:
    """The erroneous SHA af772e60... must not appear in the §8 body.

    The §15 amendment record legitimately cites af772e60... as historical
    evidence (old→new correction table).  We extract only the §8 section
    and check that no data cell there still holds the wrong value.
    """
    bad = "af772e60f96c70a6601705eae49c039dd492b0050c0973821fe7575354c15b24"
    text = MANIFEST_PATH.read_text(encoding="utf-8")

    # Extract §8 body: from "## 8." up to the next "## " heading.
    lines = text.splitlines()
    in_s8, s8_lines = False, []
    for line in lines:
        if line.startswith("## 8."):
            in_s8 = True
        elif in_s8 and line.startswith("## "):
            break
        if in_s8:
            s8_lines.append(line)

    assert s8_lines, "§8 section not found in manifest"
    s8_text = "\n".join(s8_lines)
    assert bad not in s8_text, (
        f"Erroneous SHA {bad[:16]}… still present in §8.\n"
        f"§8 should contain only the corrected SHA {FROZEN_PROMPT_SHA256[:16]}…"
    )
