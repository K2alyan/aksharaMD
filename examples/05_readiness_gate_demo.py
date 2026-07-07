"""
Readiness-gate ingestion demo.

Shows how to implement a policy-based readiness gate before sending documents
to a vector store or RAG pipeline.

Key concept: the readiness score measures extraction quality.  Whether a
document passes the gate depends on the pipeline's *policy threshold*, which
is a separate, team-defined decision.  A document scoring 93/HIGH is a good
extraction — it may still be held by a strict pipeline policy.

Two policies are demonstrated side by side:

  Policy A — standard production ingestion (threshold ≥ 85)
    All HIGH documents pass.  This is the recommended threshold for most
    production RAG pipelines.

  Policy B — strict demo gate (threshold ≥ 94)
    Only documents scoring ≥ 94 are embedded.  The stub document (93/HIGH)
    meets the extraction quality bar but does not meet this stricter policy,
    so it is routed to a review queue rather than embedded automatically.

CLI equivalents:
    aksharamd compile doc.pdf --json
    aksharamd compile doc.pdf --json --min-readiness-score 85

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


# ── policy thresholds ─────────────────────────────────────────────────────────

# Recommended for most production RAG pipelines.
PRODUCTION_THRESHOLD = 85

# Intentionally strict threshold used in the second demo pass to show routing
# behavior.  Not a recommendation — common real-world values are 85 (strict)
# and 70 (internal search / lenient ingestion).
STRICT_DEMO_THRESHOLD = 94


# ── mock pipeline steps ───────────────────────────────────────────────────────

def mock_embed(doc_name: str, chunks: int, score: int) -> None:
    """Stand-in for your actual vector-store upsert call."""
    print(f"    [embed]  {doc_name}: {chunks} chunk(s) → vector store  (score {score})")


def mock_review_queue(doc_name: str, score: int, threshold: int) -> None:
    """Stand-in for routing a document to a human review queue."""
    print(
        f"    [review] {doc_name}: score {score} does not meet this pipeline's "
        f"policy threshold of {threshold} — routed for manual review"
    )


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


def _print_table(results: list[dict], threshold: int) -> tuple[list[dict], list[dict]]:
    print(f"{'Document':<28} {'Score':>6}  {'Band':<6}  {'Chunks':>6}  {'Tokens':>7}  Result")
    print("-" * 76)
    passed, held = [], []
    for r in results:
        status = "PASS" if r["success"] else "HOLD"
        band = r["quality_band"] or "?"
        print(
            f"{r['name']:<28} {r['readiness_score']:>6}  {band:<6}  "
            f"{r['chunks']:>6}  {r['optimized_tokens']:>7}  {status}"
        )
        if r["warning_codes"]:
            print(f"  warnings: {', '.join(r['warning_codes'])}")
        if r["errors"]:
            print(f"  errors:   {'; '.join(r['errors'])}")
        (passed if r["success"] else held).append(r)
    return passed, held


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
                # A one-word placeholder.  Extracts cleanly (score ~93, HIGH band)
                # but is intentionally thin — useful for showing how a strict
                # pipeline policy can route incomplete stubs to review even when
                # the extraction itself produced no errors.
                "TODO",
            ),
        ]

        sources = []
        for filename, content in docs:
            p = Path(tmp) / filename
            p.write_text(content, encoding="utf-8")
            sources.append(str(p))

        # Compile all documents once; reuse results for both policy passes.
        compiled = [run_gate(src, compiler, threshold=0) for src in sources]

        # ── Policy A: standard production ingestion ───────────────────────────

        print("=" * 76)
        print(f"Policy A — standard production ingestion  (threshold >= {PRODUCTION_THRESHOLD})")
        print("  Recommended for most production RAG pipelines.")
        print("=" * 76)

        results_a = [
            {**r, "success": not r["errors"] and r["readiness_score"] >= PRODUCTION_THRESHOLD}
            for r in compiled
        ]
        passed_a, held_a = _print_table(results_a, PRODUCTION_THRESHOLD)

        print(f"\nGate summary: {len(passed_a)} passed, {len(held_a)} held\n")
        for r in passed_a:
            mock_embed(r["name"], r["chunks"], r["readiness_score"])
        for r in held_a:
            mock_review_queue(r["name"], r["readiness_score"], PRODUCTION_THRESHOLD)

        # ── Policy B: strict demo gate ────────────────────────────────────────

        print()
        print("=" * 76)
        print(f"Policy B — strict demo gate  (threshold >= {STRICT_DEMO_THRESHOLD})")
        print(
            "  Intentionally strict to show routing behavior.  stub.txt scores\n"
            f"  ~93/HIGH — a clean extraction — but does not meet this pipeline's\n"
            f"  policy of >= {STRICT_DEMO_THRESHOLD}.  The extraction is fine; the policy is strict."
        )
        print("=" * 76)

        results_b = [
            {**r, "success": not r["errors"] and r["readiness_score"] >= STRICT_DEMO_THRESHOLD}
            for r in compiled
        ]
        passed_b, held_b = _print_table(results_b, STRICT_DEMO_THRESHOLD)

        print(f"\nGate summary: {len(passed_b)} passed, {len(held_b)} held for review\n")
        for r in passed_b:
            mock_embed(r["name"], r["chunks"], r["readiness_score"])
        for r in held_b:
            mock_review_queue(r["name"], r["readiness_score"], STRICT_DEMO_THRESHOLD)

        # ── JSON output example ───────────────────────────────────────────────

        print()
        print("=" * 76)
        print("JSON result for first document (mirrors `aksharamd compile --json`):")
        print("=" * 76)
        print(json.dumps(compiled[0], indent=2))

        # ── threshold reference ───────────────────────────────────────────────

        print()
        print("Threshold reference:")
        print("  >= 85  HIGH band   — recommended for strict production ingestion")
        print("  >= 70  OK band     — acceptable for internal search / lenient ingestion")
        print("  <  70  RISKY/POOR  — investigate before embedding")


if __name__ == "__main__":
    main()
