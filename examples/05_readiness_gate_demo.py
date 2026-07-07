"""
Readiness-gate ingestion demo.

Shows how to implement a readiness gate before sending documents to a vector
store or RAG pipeline.  Each document is compiled, scored, and either passed
to a mock embed step or blocked, depending on whether its readiness score
meets the threshold.

This mirrors what the CLI flags do:

    aksharamd compile doc.pdf --json
    aksharamd compile doc.pdf --json --min-readiness-score 70

Usage:
    python examples/05_readiness_gate_demo.py
"""

from __future__ import annotations

import sys
import tempfile
import json
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from aksharamd.compiler import Compiler


# ── configuration ─────────────────────────────────────────────────────────────

# Threshold chosen so the stub fixture (one-word text, score ~93) is blocked
# while the substantive markdown documents (score ~95) pass.  In production,
# HIGH (>=85) is appropriate for strict ingestion; OK (>=70) for internal search.
MIN_READINESS_SCORE = 94


# ── mock embed step ───────────────────────────────────────────────────────────

def mock_embed(doc_name: str, chunks: int, score: int) -> None:
    """Stand-in for your actual vector-store upsert call."""
    print(f"    [embed] {doc_name}: {chunks} chunk(s) sent to vector store  (score {score})")


# ── gate logic ────────────────────────────────────────────────────────────────

def run_gate(source: str, compiler: Compiler, threshold: int) -> dict:
    """Compile one document and return a structured result dict."""
    _, ctx = compiler.compile_to_string(source)
    m = ctx.manifest
    v = ctx.validation

    warning_codes = [w.code for w in v.warnings]
    errors = [e.message for e in v.errors]
    passed = not errors and m.readiness_score >= threshold

    return {
        "source": source,
        "name": Path(source).name,
        "readiness_score": m.readiness_score,
        "quality_band": m.quality_band,
        "chunks": m.chunks,
        "pages": m.pages,
        "optimized_tokens": m.optimized_tokens,
        "warning_codes": warning_codes,
        "errors": errors,
        "success": passed,
    }


# ── demo ──────────────────────────────────────────────────────────────────────

def main() -> None:
    compiler = Compiler(output_dir=None)  # no output files written

    # Create temporary demo documents in-process — no external files required.
    with tempfile.TemporaryDirectory() as tmp:
        docs: list[tuple[str, str]] = [
            (
                "policy_handbook.md",
                "# Employee Policy Handbook\n\n"
                "## 1. Code of Conduct\n\n"
                "All employees are expected to act with integrity and professionalism "
                "in all business interactions.  Violations of this policy may result "
                "in disciplinary action up to and including termination.\n\n"
                "## 2. Remote Work Policy\n\n"
                "Employees approved for remote work must maintain a dedicated workspace "
                "free from distraction.  Core hours are 10 am – 3 pm in the employee's "
                "local timezone.  Equipment is provided by the company and must be "
                "returned upon separation.\n\n"
                "## 3. Data Security\n\n"
                "Confidential data must not be stored on personal devices.  All laptops "
                "are encrypted using full-disk encryption.  Passwords must be at least "
                "14 characters and rotated every 90 days.\n",
            ),
            (
                "release_notes.md",
                "# Release Notes — v2.4.1\n\n"
                "## Bug fixes\n\n"
                "- Fixed null pointer in payment gateway timeout handler.\n"
                "- Corrected date formatting for ISO 8601 timestamps in API responses.\n"
                "- Resolved race condition in background job scheduler under high load.\n\n"
                "## Improvements\n\n"
                "- Reduced average API response time by 18% through query plan caching.\n"
                "- Added structured logging to all authentication events.\n\n"
                "## Breaking changes\n\n"
                "None in this release.\n",
            ),
            (
                "stub.txt",
                # Intentionally minimal — likely to score below threshold.
                "TODO",
            ),
        ]

        sources = []
        for filename, content in docs:
            p = Path(tmp) / filename
            p.write_text(content, encoding="utf-8")
            sources.append(str(p))

        print(f"Readiness gate threshold: {MIN_READINESS_SCORE}/100\n")
        print(f"{'Document':<28} {'Score':>6}  {'Band':<6}  {'Chunks':>6}  {'Tokens':>7}  Result")
        print("-" * 76)

        passed_docs: list[dict] = []
        blocked_docs: list[dict] = []

        for source in sources:
            result = run_gate(source, compiler, MIN_READINESS_SCORE)

            status = "PASS" if result["success"] else "BLOCK"
            band = result["quality_band"] or "?"
            print(
                f"{result['name']:<28} {result['readiness_score']:>6}  {band:<6}  "
                f"{result['chunks']:>6}  {result['optimized_tokens']:>7}  {status}"
            )
            if result["warning_codes"]:
                print(f"  warnings: {', '.join(result['warning_codes'])}")
            if result["errors"]:
                print(f"  errors:   {'; '.join(result['errors'])}")

            if result["success"]:
                passed_docs.append(result)
            else:
                blocked_docs.append(result)

        # ── embed step (only for documents that passed the gate) ──────────────

        print(f"\n{'─' * 76}")
        print(f"Gate summary: {len(passed_docs)} passed, {len(blocked_docs)} blocked\n")

        if passed_docs:
            print("Embedding passed documents:")
            for r in passed_docs:
                mock_embed(r["name"], r["chunks"], r["readiness_score"])

        if blocked_docs:
            print("\nBlocked documents (not embedded):")
            for r in blocked_docs:
                reason = (
                    f"score {r['readiness_score']} < threshold {MIN_READINESS_SCORE}"
                    if not r["errors"]
                    else f"errors: {'; '.join(r['errors'])}"
                )
                print(f"    [block] {r['name']}: {reason}")

        # ── JSON output example ───────────────────────────────────────────────

        print("\n" + "─" * 76)
        print("JSON result for first document (mirrors --json CLI output):\n")
        first = run_gate(sources[0], compiler, MIN_READINESS_SCORE)
        print(json.dumps(first, indent=2))


if __name__ == "__main__":
    main()
