"""Guardrail tests locking in the CVE-2026-5241 reachability assessment.

If any of these tests start failing, the reachability assumption underlying
the accept-with-justification disposition of Dependabot alert #9 has
regressed and MUST be re-assessed before merging. See SECURITY.md for the
full evidence-based review.
"""
from __future__ import annotations

import pathlib
import re

PROD_ROOT = pathlib.Path(__file__).resolve().parents[1] / "aksharamd"

# Unlimited-OCR is the only production code path allowed to construct
# transformers loaders with ``trust_remote_code=True``. That path uses:
#
#   * a pinned model revision (see ``_UNLIMITED_OCR_MODEL_REVISION`` in
#     ``aksharamd/plugins/ocr_backends/unlimited_ocr/adapter.py``),
#   * a byte-level trusted-code manifest (``unlimited_ocr_trusted_
#     manifest.json``) verified before load,
#   * an audited module-local eval override in ``eval_override.py``,
#   * a full static review documented in
#     ``docs/security/unlimited_ocr_static_review_d549bb9d.md``.
#
# Any NEW production code that reaches these patterns is a regression
# that must fail this test. If a legitimate second consumer appears,
# add it to this allowlist explicitly and expand the reachability
# review in SECURITY.md — do not silently widen the scope.
_ALLOWED_PATHS: frozenset[str] = frozenset({
    # Unlimited-OCR pinned model loader.
    "plugins/ocr_backends/unlimited_ocr/adapter.py",
    # Module-local eval override for the pinned model's remote-code
    # execution surface. Its whole purpose is to sit inside the
    # ``trust_remote_code=True`` code path with a minimal, audited scope.
    "plugins/ocr_backends/eval_override.py",
})

# Regexes that would reach the LightGlue vulnerability if combined with an
# attacker-controlled model repository ID.  Each pattern targets the
# executable form (keyword-argument or method call) rather than the bare
# identifier, so that SECURITY.md-style discussion in comments and
# docstrings does not trip the guard.
_DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("trust_remote_code=True", r"\btrust_remote_code\s*=\s*True\b"),
    ("AutoModel.from_pretrained(", r"\bAutoModel\.from_pretrained\s*\("),
    ("AutoConfig.from_pretrained(", r"\bAutoConfig\.from_pretrained\s*\("),
    ("LightGlue class use", r"\bLightGlue(?:Config|Model|ForKeypointMatching)?\s*\("),
    ("LightGlue import", r"^\s*(?:from|import)\s+.*\bLightGlue"),
)


def _iter_prod_source() -> list[pathlib.Path]:
    return [
        p for p in PROD_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
        and p.relative_to(PROD_ROOT).as_posix() not in _ALLOWED_PATHS
    ]


def _strip_line_comments(text: str) -> str:
    """Drop everything after an un-escaped `#` on each line so that safety
    discussion in comments does not trip the executable-code guard."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0]
        out.append(stripped)
    return "\n".join(out)


def test_no_dangerous_transformers_calls_in_production_source() -> None:
    """`aksharamd/` production source must not contain any executable
    pattern that could reach transformers CVE-2026-5241.  This is a
    code-hygiene guard, not a substitute for a full reachability review."""
    hits: dict[str, list[str]] = {label: [] for label, _ in _DANGEROUS_PATTERNS}
    for path in _iter_prod_source():
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = _strip_line_comments(raw)
        for label, pattern in _DANGEROUS_PATTERNS:
            for match in re.finditer(pattern, text, flags=re.MULTILINE):
                lineno = text.count("\n", 0, match.start()) + 1
                hits[label].append(f"{path.relative_to(PROD_ROOT)}:{lineno}")
    for label, occurrences in hits.items():
        assert not occurrences, (
            f"Regression: {label} appeared in production source at "
            f"{occurrences}. This may reintroduce the CVE-2026-5241 "
            "attack surface. See SECURITY.md 'Deferred Dependency Alerts' "
            "and re-run the reachability review before shipping."
        )


def test_marker_integration_uses_no_argument_create_model_dict() -> None:
    """`aksharamd/plugins/parsers/pdf.py` must call `create_model_dict()`
    with no arguments so no user-controllable model identifier can reach
    the transformers loader."""
    pdf_parser = PROD_ROOT / "plugins" / "parsers" / "pdf.py"
    text = pdf_parser.read_text(encoding="utf-8")
    # A single-line arg-free call is the only allowed pattern.
    ok = "create_model_dict()" in text
    assert ok, (
        "Expected `create_model_dict()` (no arguments) in pdf.py; "
        "an argument-taking form would broaden the model-selection surface "
        "and requires a fresh CVE-2026-5241 reachability review."
    )
    # And there must be NO argument-taking call.
    bad = re.search(r"create_model_dict\(\s*[^)\s]", text)
    assert bad is None, (
        f"Found `create_model_dict(<arg>)` in pdf.py at offset {bad.start() if bad else '?'}. "
        "Passing arguments here can broaden the attack surface. "
        "Re-run the reachability review before shipping."
    )
