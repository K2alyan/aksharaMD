"""Evaluator: run AksharaMD on a PDF and score with MGAM against a reference."""
from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from .mgam import MGAMResult, extract_reference_blocks, mgam_score


# ── Reference extraction ───────────────────────────────────────────────────────

def _pymupdf_raw_text(pdf_path: Path) -> str:
    """Extract plain text from every page using PyMuPDF as oracle reference."""
    doc = fitz.open(str(pdf_path))
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
    doc.close()
    return "\n\n".join(parts)


def get_reference_blocks(pdf_path: Path, ref_file: Path | None = None) -> list[str]:
    """Return reference blocks for a PDF.

    Priority:
    1. Caller-supplied ref_file
    2. <pdf_stem>.ref.txt in the same directory
    3. PyMuPDF raw text extraction (oracle fallback)
    """
    if ref_file is None:
        candidate = pdf_path.with_suffix(".ref.txt")
        if candidate.exists():
            ref_file = candidate

    if ref_file is not None:
        raw = ref_file.read_text(encoding="utf-8")
    else:
        raw = _pymupdf_raw_text(pdf_path)

    return extract_reference_blocks(raw)


# ── AksharaMD prediction extraction ───────────────────────────────────────────

def _aksharamd_blocks(pdf_path: Path, tmp_dir: Path) -> tuple[list[str], float, list[str]]:
    """Run AksharaMD on pdf_path, return (text_blocks, elapsed_s, warnings).

    Each Block's content is returned as one element — TABLE blocks have their
    pipe markdown stripped so they compare cleanly against reference prose.
    """
    from aksharamd.compiler import Compiler
    from aksharamd.models.block import BlockType

    out = str(tmp_dir / pdf_path.stem)
    t0 = time.perf_counter()
    try:
        ctx = Compiler(output_dir=out).compile(str(pdf_path))
        elapsed = time.perf_counter() - t0
    except Exception as exc:
        return [], time.perf_counter() - t0, [str(exc)]

    if ctx.document is None:
        return [], elapsed, ["no document produced"]

    skipped = {BlockType.IMAGE}
    blocks = []
    for blk in ctx.document.blocks:
        if blk.type in skipped:
            continue
        blocks.append(blk.content)

    warnings = [w.message for w in ctx.validation.warnings]
    return blocks, elapsed, warnings


# ── Per-document result ────────────────────────────────────────────────────────

@dataclass
class DocumentResult:
    name: str
    mgam: MGAMResult
    elapsed_s: float
    ref_block_count: int
    pred_block_count: int
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def evaluate_document(
    pdf_path: Path,
    ref_file: Path | None = None,
    max_merge: int = 8,
) -> DocumentResult:
    """Run AksharaMD on one PDF and return a DocumentResult with MGAM scores."""
    ref_blocks = get_reference_blocks(pdf_path, ref_file)

    with tempfile.TemporaryDirectory() as tmp:
        pred_blocks, elapsed, warnings = _aksharamd_blocks(pdf_path, Path(tmp))

    if not pred_blocks:
        error = warnings[0] if warnings else "empty output"
        empty = MGAMResult(0.0, 0.0, 0.0, [0.0] * len(ref_blocks), [])
        return DocumentResult(
            name=pdf_path.name,
            mgam=empty,
            elapsed_s=elapsed,
            ref_block_count=len(ref_blocks),
            pred_block_count=0,
            error=error,
        )

    result = mgam_score(ref_blocks, pred_blocks, max_merge=max_merge)
    return DocumentResult(
        name=pdf_path.name,
        mgam=result,
        elapsed_s=elapsed,
        ref_block_count=len(ref_blocks),
        pred_block_count=len(pred_blocks),
        warnings=warnings,
    )


# ── Corpus evaluation ──────────────────────────────────────────────────────────

@dataclass
class CorpusResult:
    documents: list[DocumentResult]
    mean_recall: float
    mean_precision: float
    mean_f1: float
    n_total: int
    n_errors: int


def evaluate_corpus(
    pdf_dir: Path,
    ref_dir: Path | None = None,
    max_merge: int = 8,
    progress_callback=None,
) -> CorpusResult:
    """Evaluate all PDFs in pdf_dir with MGAM scoring.

    ref_dir: optional directory of <name>.ref.txt reference files.
             Falls back to colocated .ref.txt files, then PyMuPDF oracle.
    """
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise ValueError(f"No PDFs found in {pdf_dir}")

    results = []
    for i, pdf in enumerate(pdfs):
        if progress_callback:
            progress_callback(i + 1, len(pdfs), pdf.name)

        ref_file = None
        if ref_dir is not None:
            candidate = ref_dir / (pdf.stem + ".ref.txt")
            if candidate.exists():
                ref_file = candidate

        doc_result = evaluate_document(pdf, ref_file=ref_file, max_merge=max_merge)
        results.append(doc_result)

    good = [r for r in results if r.error is None]
    mean_recall = sum(r.mgam.recall for r in good) / len(good) if good else 0.0
    mean_precision = sum(r.mgam.precision for r in good) / len(good) if good else 0.0
    mean_f1 = sum(r.mgam.f1 for r in good) / len(good) if good else 0.0

    return CorpusResult(
        documents=results,
        mean_recall=mean_recall,
        mean_precision=mean_precision,
        mean_f1=mean_f1,
        n_total=len(results),
        n_errors=len(results) - len(good),
    )
