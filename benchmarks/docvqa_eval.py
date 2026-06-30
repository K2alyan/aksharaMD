#!/usr/bin/env python3
"""
DocVQA evaluation pipeline for AksharaMD.

Converts document images from the DocVQA benchmark with each tool, asks Claude
each VQA question using the converted text, and scores with ANLS.

Usage:
    python -m benchmarks.docvqa_eval --n 100
    python -m benchmarks.docvqa_eval --n 100 --tools aksharamd markitdown
    python -m benchmarks.docvqa_eval --n 50 --no-llm   # conversion only, no QA

Requires:
    pip install anthropic datasets python-Levenshtein
    ANTHROPIC_API_KEY set in environment (for QA step)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

RESULTS_PATH = Path("benchmark_results/docvqa_results.json")
DEFAULT_TOOLS = ["aksharamd", "markitdown", "docling"]
_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


# ── ANLS metric ───────────────────────────────────────────────────────────────

def _anls(pred: str, gts: list[str], threshold: float = 0.5) -> float:
    from Levenshtein import distance as lev
    scores = []
    for gt in gts:
        p, g = pred.lower().strip(), gt.lower().strip()
        if not g:
            continue
        nls = 1.0 - lev(p, g) / max(len(p), len(g), 1)
        scores.append(nls if nls >= threshold else 0.0)
    return max(scores) if scores else 0.0


# ── tool runners (image → text) ───────────────────────────────────────────────

def _convert_aksharamd(img_path: str) -> tuple[str, float]:
    from benchmarks.corpus_benchmark import _run_aksharamd
    return _run_aksharamd(Path(img_path))


def _convert_markitdown(img_path: str) -> tuple[str, float]:
    from benchmarks.corpus_benchmark import _run_markitdown
    return _run_markitdown(Path(img_path))


def _convert_docling(img_path: str) -> tuple[str, float]:
    from benchmarks.corpus_benchmark import _run_docling
    return _run_docling(Path(img_path))


CONVERTERS = {
    "aksharamd":  _convert_aksharamd,
    "markitdown": _convert_markitdown,
    "docling":    _convert_docling,
}


# ── LLM QA ────────────────────────────────────────────────────────────────────

def _ask_claude(question: str, doc_text: str) -> str:
    import anthropic
    if len(doc_text.strip()) < 20:
        return "N/A"
    client = anthropic.Anthropic()
    prompt = (
        "Answer the following question based ONLY on the document text provided.\n"
        "Be concise — answer with just the answer value, no explanation.\n\n"
        f"Question: {question}\n\n"
        f"Document text:\n{doc_text[:3000]}"
    )
    msg = client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=64,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="DocVQA evaluation pipeline")
    parser.add_argument("--n", type=int, default=100,
                        help="Number of DocVQA examples to evaluate (default: 100)")
    parser.add_argument("--tools", nargs="+", default=DEFAULT_TOOLS,
                        choices=list(CONVERTERS),
                        help="Tools to evaluate")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM QA step; report conversion stats only")
    parser.add_argument("--dataset", default="nielsr/docvqa_1200_examples",
                        help="HuggingFace dataset name (default: nielsr/docvqa_1200_examples)")
    args = parser.parse_args()

    use_llm = not args.no_llm
    if use_llm:
        try:
            import anthropic as _a; _a.Anthropic()
        except Exception:
            print("Note: Anthropic API not available — running conversion only (no QA scoring).")
            use_llm = False

    # Load dataset
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: pip install datasets", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {args.dataset} …", flush=True)
    try:
        ds = load_dataset(args.dataset, split="train", trust_remote_code=True)
    except Exception as exc:
        print(f"ERROR: Failed to load dataset: {exc}", file=sys.stderr)
        sys.exit(1)

    n = min(args.n, len(ds))
    print(f"Evaluating {n} examples with tools: {', '.join(args.tools)}")
    if use_llm:
        print(f"QA model: {_ANTHROPIC_MODEL}")
    print()

    # Per-tool accumulators
    tool_anls:    dict[str, list[float]] = {t: [] for t in args.tools}
    tool_tokens:  dict[str, list[int]]   = {t: [] for t in args.tools}
    tool_success: dict[str, int]         = {t: 0 for t in args.tools}
    tool_fail:    dict[str, int]         = {t: 0 for t in args.tools}

    all_results = []

    for i, example in enumerate(ds.select(range(n))):
        question   = example.get("question", "")
        answers    = example.get("answers", []) or example.get("answer", [])
        if isinstance(answers, str):
            answers = [answers]
        image = example.get("image")  # PIL Image

        if image is None or not question:
            continue

        # Save image to temp PNG
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            image.save(tmp_path)
        except Exception:
            os.unlink(tmp_path)
            continue

        doc_result: dict = {"index": i, "question": question, "answers": answers, "tools": {}}

        for tool in args.tools:
            converter = CONVERTERS[tool]
            try:
                text, elapsed = converter(tmp_path)
                tokens = len(text.split())
                tool_tokens[tool].append(tokens)
                tool_success[tool] += 1
            except Exception:
                text, elapsed, tokens = "", 0.0, 0
                tool_fail[tool] += 1

            anls_score = 0.0
            prediction = ""
            if use_llm and text:
                try:
                    prediction = _ask_claude(question, text)
                    anls_score = _anls(prediction, answers)
                    time.sleep(0.3)
                except Exception:
                    prediction = ""
                    anls_score = 0.0
            if use_llm:
                tool_anls[tool].append(anls_score)

            doc_result["tools"][tool] = {
                "tokens": tokens,
                "elapsed": round(elapsed, 3),
                "prediction": prediction,
                "anls": round(anls_score, 4),
            }

            status = f"anls={anls_score:.3f}" if use_llm else f"{tokens} tok"
            print(f"  [{i+1}/{n}] {tool:<15} {status}")

        os.unlink(tmp_path)
        all_results.append(doc_result)

    # Summary
    print(f"\nDocVQA Evaluation — {n} documents")
    header = f"{'Tool':<15} {'ANLS':>8}  {'Avg Tokens':>12}  {'Success':>10}"
    print(header)
    print("-" * len(header))
    for tool in args.tools:
        anls_scores = tool_anls[tool]
        tokens      = tool_tokens[tool]
        avg_anls    = sum(anls_scores) / len(anls_scores) if anls_scores else 0.0
        avg_tok     = sum(tokens) / len(tokens) if tokens else 0
        succ        = tool_success[tool]
        fail        = tool_fail[tool]
        anls_str    = f"{avg_anls:.3f}" if use_llm else "N/A"
        print(f"{tool:<15} {anls_str:>8}  {avg_tok:>12,.0f}  {succ:>4}/{succ+fail}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({
        "dataset": args.dataset,
        "n": n,
        "tools": args.tools,
        "llm_qa": use_llm,
        "qa_model": _ANTHROPIC_MODEL if use_llm else None,
        "summary": {
            t: {
                "avg_anls": round(sum(tool_anls[t]) / len(tool_anls[t]), 4) if tool_anls[t] else None,
                "avg_tokens": round(sum(tool_tokens[t]) / len(tool_tokens[t])) if tool_tokens[t] else 0,
                "success": tool_success[t],
                "fail": tool_fail[t],
            }
            for t in args.tools
        },
        "results": all_results,
    }, indent=2))
    print(f"\nResults saved → {RESULTS_PATH}")


if __name__ == "__main__":
    main()
