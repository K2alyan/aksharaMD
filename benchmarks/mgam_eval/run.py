"""
MGAM Standalone Evaluator — CLI entry point.

Usage examples:

  # Evaluate a directory of PDFs against PyMuPDF oracle references
  python -m benchmarks.mgam_eval.run path/to/pdfs/

  # Evaluate with explicit .ref.txt reference files in a separate directory
  python -m benchmarks.mgam_eval.run path/to/pdfs/ --refs path/to/refs/

  # Build + immediately evaluate the bundled synthetic corpus
  python -m benchmarks.mgam_eval.run --corpus

  # Write results to JSON
  python -m benchmarks.mgam_eval.run path/to/pdfs/ --json results.json

  # Show per-block scores for a single document (diagnostic mode)
  python -m benchmarks.mgam_eval.run path/to/pdfs/ --verbose
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .evaluator import CorpusResult, DocumentResult, evaluate_corpus


# ── Formatting ────────────────────────────────────────────────────────────────

_BAR_WIDTH = 30


def _bar(score: float, width: int = _BAR_WIDTH) -> str:
    filled = int(round(score * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _pct(score: float) -> str:
    return f"{score * 100:5.1f}%"


def _print_document(doc: DocumentResult, verbose: bool = False) -> None:
    print(f"\n  {doc.name}")
    if doc.error:
        print(f"    ERROR: {doc.error}")
        return
    m = doc.mgam
    print(f"    Recall    {_pct(m.recall)}  {_bar(m.recall)}  ({doc.ref_block_count} ref blocks)")
    print(f"    Precision {_pct(m.precision)}  {_bar(m.precision)}  ({doc.pred_block_count} pred blocks)")
    print(f"    F1        {_pct(m.f1)}  {_bar(m.f1)}")
    print(f"    Elapsed   {doc.elapsed_s:.2f}s")
    if doc.warnings:
        for w in doc.warnings:
            print(f"    WARN: {w[:120]}")
    if verbose and m.per_ref_scores:
        print("    Per-reference-block scores:")
        for i, s in enumerate(m.per_ref_scores):
            flag = " <-- LOW" if s < 0.5 else ""
            print(f"      [{i:3d}] {_pct(s)}{flag}")


def _print_corpus(corpus: CorpusResult) -> None:
    w = 50
    print("\n" + "=" * w)
    print(f"  AGGREGATE  ({corpus.n_total} document{'s' if corpus.n_total != 1 else ''})")
    print("=" * w)
    print(f"  Mean Recall    {_pct(corpus.mean_recall)}  {_bar(corpus.mean_recall)}")
    print(f"  Mean Precision {_pct(corpus.mean_precision)}  {_bar(corpus.mean_precision)}")
    print(f"  Mean F1        {_pct(corpus.mean_f1)}  {_bar(corpus.mean_f1)}")
    if corpus.n_errors:
        print(f"  Errors: {corpus.n_errors}/{corpus.n_total}")
    print("=" * w)


def _to_json(corpus: CorpusResult) -> dict:
    return {
        "aggregate": {
            "mean_recall": round(corpus.mean_recall, 4),
            "mean_precision": round(corpus.mean_precision, 4),
            "mean_f1": round(corpus.mean_f1, 4),
            "n_total": corpus.n_total,
            "n_errors": corpus.n_errors,
        },
        "documents": [
            {
                "name": d.name,
                "recall": round(d.mgam.recall, 4) if not d.error else None,
                "precision": round(d.mgam.precision, 4) if not d.error else None,
                "f1": round(d.mgam.f1, 4) if not d.error else None,
                "ref_blocks": d.ref_block_count,
                "pred_blocks": d.pred_block_count,
                "elapsed_s": round(d.elapsed_s, 3),
                "error": d.error,
                "warnings": d.warnings,
            }
            for d in corpus.documents
        ],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate AksharaMD content recall with MGAM scoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "pdf_dir",
        nargs="?",
        help="Directory of PDFs to evaluate (omit if --corpus is used)",
    )
    parser.add_argument(
        "--refs",
        metavar="DIR",
        help="Directory of <name>.ref.txt reference files",
    )
    parser.add_argument(
        "--corpus",
        action="store_true",
        help="Build and evaluate the bundled synthetic corpus",
    )
    parser.add_argument(
        "--max-merge",
        type=int,
        default=8,
        metavar="N",
        help="Maximum consecutive blocks to merge when searching for a match (default: 8)",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        help="Write results to a JSON file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-block scores for each document",
    )
    args = parser.parse_args(argv)

    if args.corpus:
        from .make_corpus import build_corpus
        with tempfile.TemporaryDirectory() as tmp:
            corpus_dir = Path(tmp)
            print("Building synthetic corpus…")
            build_corpus(corpus_dir)
            print(f"\nEvaluating {len(list(corpus_dir.glob('*.pdf')))} PDFs…")
            return _run(corpus_dir, None, args)

    if not args.pdf_dir:
        parser.error("pdf_dir is required unless --corpus is used")

    pdf_dir = Path(args.pdf_dir)
    if not pdf_dir.exists():
        print(f"Error: directory not found: {pdf_dir}", file=sys.stderr)
        return 1

    refs_dir = Path(args.refs) if args.refs else None
    return _run(pdf_dir, refs_dir, args)


def _run(pdf_dir: Path, refs_dir, args) -> int:
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {pdf_dir}", file=sys.stderr)
        return 1

    print(f"\nMGAM Evaluator — {len(pdfs)} PDF(s) in {pdf_dir.name}/")
    if refs_dir:
        print(f"References: {refs_dir}")
    else:
        print("References: colocated .ref.txt files, then PyMuPDF oracle")
    print(f"Max merge:  {args.max_merge} blocks\n")

    def _progress(i, total, name):
        print(f"  [{i}/{total}] {name}…", end="\r", flush=True)

    corpus = evaluate_corpus(
        pdf_dir,
        ref_dir=refs_dir,
        max_merge=args.max_merge,
        progress_callback=_progress,
    )
    print(" " * 60, end="\r")  # clear progress line

    for doc in corpus.documents:
        _print_document(doc, verbose=args.verbose)

    _print_corpus(corpus)

    if args.json:
        out = Path(args.json)
        out.write_text(json.dumps(_to_json(corpus), indent=2), encoding="utf-8")
        print(f"\nResults written to {out}")

    return 1 if corpus.n_errors == corpus.n_total else 0


if __name__ == "__main__":
    sys.exit(main())
