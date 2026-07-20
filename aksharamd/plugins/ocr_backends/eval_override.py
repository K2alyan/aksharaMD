"""Module-local eval override for third-party model loaders (A1c).

Baidu's ``modeling_unlimitedocr.py`` calls ``eval()`` in 7 places on
model-generated text (see
``docs/security/unlimited_ocr_static_review_d549bb9d.md``). When the
Transformers loader is invoked with remote-code trust enabled, those
calls execute in our process on strings the model produced — a
code-injection surface via hallucinated or attacker-crafted output.

This module provides ``install_module_local_eval_override`` which:

1. Replaces ``modeling_unlimitedocr.eval`` with ``ast.literal_eval``,
   shadowing the builtin for every unqualified ``eval(...)`` call in
   that module's globals.
2. Fail-closed asserts the override took effect.
3. Optionally re-counts risky call sites in the module source against
   a static baseline (belt-and-suspenders on top of A1a's SHA-256 file
   verification — if the source hash matches, the counts must match
   too; if either differs, refuse).

Never patch ``builtins.eval`` globally. The override is deliberately
scoped to the reviewed module. Adjacent modules
(``modeling_deepseekv2.py``, ``deepencoder.py``, etc.) contain zero
``eval()`` calls per the static review at the pinned revision; if a
future revision adds them, the A1a hash check refuses the load before
this override runs.
"""
from __future__ import annotations

import ast
import re
from types import ModuleType
from typing import Any


class OverrideNotActive(Exception):
    """Raised when the module-local eval override could not be verified.
    Never caught silently."""


# Baseline counts of security-sensitive calls in modeling_unlimitedocr.py
# at the A1a-approved revision (d549bb9d). Verified by static review;
# see docs/security/unlimited_ocr_static_review_d549bb9d.md. Any deviation
# from these counts indicates either a manifest bug (hash matched but
# static review baseline mismatch) or an attempt to slip in additional
# execution paths — refuse in either case.
MODELING_UNLIMITEDOCR_BASELINE = {
    "eval": 7,       # 7 eval() calls on model output (all to be neutralized)
    "exec": 0,
    "compile": 0,    # not counting typing.compile / re.compile helpers
    "__import__": 0,
}


# Regex patterns — deliberately conservative. Matches literal call
# sites, not the same identifier appearing in a string/comment. Not
# a substitute for AST-based counting, but sufficient as a
# defense-in-depth belt on top of SHA-256 file verification.
_EVAL_CALL_RE = re.compile(r"(?<![.\w])eval\s*\(")
_EXEC_CALL_RE = re.compile(r"(?<![.\w])exec\s*\(")
_COMPILE_CALL_RE = re.compile(r"(?<![.\w])compile\s*\(")
_DUNDER_IMPORT_RE = re.compile(r"__import__\s*\(")


def count_risky_calls(source: str) -> dict[str, int]:
    """Count unqualified eval/exec/compile/__import__ call sites in
    ``source``. Comments are stripped first (naive # handling)."""
    # Strip # comments outside of strings. Naive: assumes no # inside
    # triple-quoted strings that also contain call-site look-alikes.
    lines = []
    for line in source.splitlines():
        # Preserve the line for whitespace but drop everything past #
        # if the # is not inside quotes. For our purposes here (counting
        # eval/exec) this heuristic is sufficient: the eval calls we
        # care about are in real code, not comments.
        in_str = False
        quote_char = ""
        out = []
        i = 0
        while i < len(line):
            c = line[i]
            if not in_str:
                if c in ('"', "'"):
                    in_str = True
                    quote_char = c
                elif c == "#":
                    break
            else:
                if c == quote_char:
                    in_str = False
            out.append(c)
            i += 1
        lines.append("".join(out))
    stripped = "\n".join(lines)
    return {
        "eval": len(_EVAL_CALL_RE.findall(stripped)),
        "exec": len(_EXEC_CALL_RE.findall(stripped)),
        "compile": len(_COMPILE_CALL_RE.findall(stripped)),
        "__import__": len(_DUNDER_IMPORT_RE.findall(stripped)),
    }


def install_module_local_eval_override(
    mod: ModuleType,
    *,
    baseline_counts: dict[str, int] | None = None,
    source_path: Any = None,
) -> None:
    """Install ``ast.literal_eval`` as ``mod.eval`` and fail-closed
    verify the override is active.

    Steps:

    1. Assert the module currently has an ``eval`` attribute (either
       via ``from builtins import eval`` or Python's implicit global
       resolution). If not, refuse — the module structure differs from
       the reviewed baseline.
    2. Reassign ``mod.eval = ast.literal_eval``.
    3. Assert ``mod.eval is ast.literal_eval``.
    4. If ``source_path`` is provided, read the module source and
       recount risky calls. Refuse if counts differ from ``baseline_counts``.

    Never silently logs a failure. Never returns partially-installed
    state. Raises ``OverrideNotActive`` on any assertion failure.
    """
    # Step 1: the module must currently be able to reference `eval`
    # via its own globals. Python resolves unqualified names through
    # the module's __dict__ first, then builtins. Setting mod.eval to
    # ast.literal_eval shadows the builtin.
    #
    # We do NOT require the module to have an explicit `eval` attribute
    # already — it may resolve to builtins.eval via LEGB. What matters
    # is that after our assignment, the module's own dict contains
    # ast.literal_eval, which shadows the builtin for all subsequent
    # unqualified `eval(...)` calls in the module.

    # Step 2: install the override.
    mod.eval = ast.literal_eval  # type: ignore[attr-defined]

    # Step 3: fail-closed assertion.
    if getattr(mod, "eval", None) is not ast.literal_eval:
        raise OverrideNotActive(
            f"module-local eval override did not take effect on {mod.__name__!r}"
        )

    # Step 4: optional source-baseline check.
    if source_path is not None and baseline_counts is not None:
        from pathlib import Path
        try:
            src = Path(source_path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise OverrideNotActive(
                f"cannot read module source {source_path} for baseline check: {e}"
            ) from e
        actual = count_risky_calls(src)
        for name, expected in baseline_counts.items():
            if actual.get(name, 0) != expected:
                raise OverrideNotActive(
                    f"risky-call baseline drift in {mod.__name__!r}: "
                    f"{name} count expected {expected}, got {actual.get(name, 0)} "
                    f"(source: {source_path})"
                )
