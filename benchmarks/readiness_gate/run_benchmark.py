#!/usr/bin/env python3
"""
Readiness-gate ingestion benchmark.

Measures whether a readiness-score gate improves retrieval quality by keeping
low-information-density documents out of the index.

Two ingestion policies are compared on a small fixed question set:

  Policy A — baseline (no gate, threshold = 0)
    All compiled documents enter the index regardless of readiness score.

  Policy B — gated (threshold = 94)
    Only documents whose readiness score meets the threshold are indexed.
    Documents below the threshold are held for review.

Retrieval uses TF-IDF cosine similarity with no external dependencies.
Results are illustrative for this specific corpus and retrieval method.

Usage:
    python -m benchmarks.readiness_gate.run_benchmark
    python benchmarks/readiness_gate/run_benchmark.py
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path

# ── configuration ─────────────────────────────────────────────────────────────

CORPUS_DIR = Path(__file__).parent / "corpus"
TOP_K = 1                   # retrieval depth: answer must appear in top-K chunks
GATE_THRESHOLD = 94         # Policy B threshold; gates out score-93 (txt) docs


# ── question set ──────────────────────────────────────────────────────────────
# Each entry: (query, answer_keywords, source_file_prefix)
# answer_keywords: ALL must appear in the retrieved chunk for a hit.

QUESTIONS: list[tuple[str, list[str], str]] = [
    (
        "How many days of annual leave do full-time employees receive?",
        ["25", "annual leave"],
        "01_employee_handbook",
    ),
    (
        "What is the main office address?",
        ["42 Innovation Drive"],
        "01_employee_handbook",
    ),
    (
        "When are performance reviews conducted each year?",
        ["March", "September"],
        "01_employee_handbook",
    ),
    (
        "What percentage of the individual health insurance premium does the company pay?",
        ["80%"],
        "01_employee_handbook",
    ),
    (
        "What is the API request timeout?",
        ["30 seconds"],
        "02_api_documentation",
    ),
    (
        "What is the default rate limit for API requests per hour?",
        ["1000 requests per hour"],
        "02_api_documentation",
    ),
    (
        "How long are API access tokens valid before they expire?",
        ["3600 seconds"],
        "02_api_documentation",
    ),
    (
        "When was Orbit Analytics version 3.2.0 released?",
        ["January 15, 2025"],
        "03_product_changelog",
    ),
    (
        "What is the maximum number of records returned per API page?",
        ["100"],
        "02_api_documentation",
    ),
    (
        "Which legacy API endpoint was deprecated in v2.8.0?",
        ["/api/v1/reports"],
        "03_product_changelog",
    ),
]


# ── TF-IDF retrieval ──────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[./][a-z0-9]+)*")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _build_index(chunks: list[tuple[str, str]]) -> dict:
    """
    Build a TF-IDF index from a list of (chunk_text, source_name) tuples.
    Returns a dict with: tokens_per_doc, idf, chunks.
    """
    n = len(chunks)
    tokens_per_doc: list[list[str]] = []
    df: Counter[str] = Counter()

    for text, _ in chunks:
        tokens = _tokenize(text)
        tokens_per_doc.append(tokens)
        df.update(set(tokens))

    idf: dict[str, float] = {
        term: math.log((n + 1) / (count + 1)) + 1.0
        for term, count in df.items()
    }
    return {"tokens_per_doc": tokens_per_doc, "idf": idf, "chunks": chunks}


def _score_chunk(query_tokens: list[str], doc_tokens: list[str], idf: dict[str, float]) -> float:
    tf = Counter(doc_tokens)
    doc_len = max(len(doc_tokens), 1)
    dot, q_norm, d_norm = 0.0, 0.0, 0.0
    for qt in set(query_tokens):
        w_idf = idf.get(qt, 1.0)
        q_w = idf.get(qt, 1.0)          # query weight = IDF
        d_w = (tf.get(qt, 0) / doc_len) * w_idf
        dot += q_w * d_w
        q_norm += q_w ** 2
        d_norm += d_w ** 2
    if q_norm == 0 or d_norm == 0:
        return 0.0
    return dot / (math.sqrt(q_norm) * math.sqrt(d_norm))


def retrieve(query: str, index: dict, k: int) -> list[tuple[float, str, str]]:
    """Return top-k (score, chunk_text, source_name) tuples."""
    q_tokens = _tokenize(query)
    scored = [
        (
            _score_chunk(q_tokens, tokens, index["idf"]),
            text,
            source,
        )
        for tokens, (text, source) in zip(index["tokens_per_doc"], index["chunks"])
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]


def hit(results: list[tuple[float, str, str]], answer_keywords: list[str]) -> bool:
    """Return True if any top-k chunk contains all answer keywords (case-insensitive)."""
    for _, text, _ in results:
        lower = text.lower()
        if all(kw.lower() in lower for kw in answer_keywords):
            return True
    return False


# ── compilation ───────────────────────────────────────────────────────────────

def compile_corpus(corpus_dir: Path) -> list[dict]:
    try:
        from aksharamd.compiler import Compiler
    except ImportError:
        print("ERROR: aksharamd is not installed. Run: pip install aksharamd")
        sys.exit(1)

    compiler = Compiler(output_dir=None)
    results = []
    for path in sorted(corpus_dir.glob("*")):
        if path.suffix in {".md", ".txt", ".html", ".pdf"}:
            _, ctx = compiler.compile_to_string(str(path))
            m = ctx.manifest
            v = ctx.validation
            chunks = [b.content for b in ctx.document.blocks if b.content.strip()]
            results.append(
                {
                    "name": path.stem,
                    "path": str(path),
                    "score": m.readiness_score,
                    "band": m.quality_band or "?",
                    "warnings": [w.code for w in v.warnings],
                    "errors": [e.message for e in v.errors],
                    "chunks": [(c, path.stem) for c in chunks],
                }
            )
    return results


# ── benchmark ─────────────────────────────────────────────────────────────────

def run(threshold: int, compiled: list[dict]) -> dict:
    """Run one ingestion policy and return retrieval metrics."""
    indexed = [doc for doc in compiled if not doc["errors"] and doc["score"] >= threshold]
    held = [doc for doc in compiled if doc["errors"] or doc["score"] < threshold]

    all_chunks: list[tuple[str, str]] = []
    for doc in indexed:
        all_chunks.extend(doc["chunks"])

    if not all_chunks:
        return {"hits": 0, "total": len(QUESTIONS), "held": len(held), "indexed": 0}

    index = _build_index(all_chunks)
    hits = 0
    per_question = []
    for query, answer_kws, _ in QUESTIONS:
        results = retrieve(query, index, TOP_K)
        h = hit(results, answer_kws)
        hits += int(h)
        per_question.append(h)

    return {
        "hits": hits,
        "total": len(QUESTIONS),
        "hit_rate": hits / len(QUESTIONS),
        "held": len(held),
        "indexed": len(indexed),
        "per_question": per_question,
    }


def main() -> None:
    print(f"Corpus: {CORPUS_DIR}")
    print(f"Retrieval depth (top-k): {TOP_K}")
    print(f"Policy B threshold: {GATE_THRESHOLD}/100\n")

    print("Compiling corpus ...")
    compiled = compile_corpus(CORPUS_DIR)

    # ── corpus summary ────────────────────────────────────────────────────────
    print(f"\n{'Document':<35} {'Score':>6}  {'Band':<6}  {'Chunks':>6}  Warnings")
    print("-" * 70)
    for doc in compiled:
        w = ", ".join(doc["warnings"]) if doc["warnings"] else "—"
        e = " [ERROR]" if doc["errors"] else ""
        print(
            f"{doc['name']:<35} {doc['score']:>6}  {doc['band']:<6}  "
            f"{len(doc['chunks']):>6}  {w}{e}"
        )

    # ── run both policies ─────────────────────────────────────────────────────
    policy_a = run(threshold=0, compiled=compiled)
    policy_b = run(threshold=GATE_THRESHOLD, compiled=compiled)

    # ── per-question breakdown ────────────────────────────────────────────────
    print(f"\n{'#':<4} {'Query (truncated)':<52} {'A':>4}  {'B':>4}")
    print("-" * 64)
    for i, (query, _, _) in enumerate(QUESTIONS):
        a_hit = "HIT " if policy_a["per_question"][i] else "miss"
        b_hit = "HIT " if policy_b["per_question"][i] else "miss"
        print(f"{i+1:<4} {query[:52]:<52} {a_hit:>4}  {b_hit:>4}")

    # ── summary table ─────────────────────────────────────────────────────────
    print()
    print("=" * 64)
    print(f"{'Policy':<28} {'Indexed':>8}  {'Held':>6}  {'Hits':>6}  {'Hit rate':>9}")
    print("-" * 64)
    print(
        f"{'A — no gate (threshold=0)':<28} "
        f"{policy_a['indexed']:>8}  {policy_a['held']:>6}  "
        f"{policy_a['hits']:>6}/{policy_a['total']}  "
        f"{policy_a['hit_rate']:>8.0%}"
    )
    print(
        f"{'B — gated (threshold=94)':<28} "
        f"{policy_b['indexed']:>8}  {policy_b['held']:>6}  "
        f"{policy_b['hits']:>6}/{policy_b['total']}  "
        f"{policy_b['hit_rate']:>8.0%}"
    )
    print("=" * 64)

    delta = policy_b["hit_rate"] - policy_a["hit_rate"]
    direction = "improvement" if delta >= 0 else "regression"
    print(f"\nGating effect: {delta:+.0%} {direction} in retrieval hit rate @ top-{TOP_K}")
    print(
        "\nNote: results reflect this specific corpus and TF-IDF retrieval method.\n"
        "Embedding-based retrieval may show different sensitivity. See README for\n"
        "methodology and limitations."
    )


if __name__ == "__main__":
    main()
