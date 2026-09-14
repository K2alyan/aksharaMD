"""Offline cross-parser consensus audit (P5).

Given a source PDF and one or more pre-parsed markdown outputs (one per
parser), compute:

  * per-parser word-count fidelity vs the source PDF (reuses the same
    metric as ``W_DROPPED_CONTENT``: `markdown_words / pdf_words`)
  * pairwise Jaccard word-set similarity across parsers (which parsers
    agree on the content, which are outliers)
  * unique-word deltas (words present in one parser's output but not
    in any other's) — surfaces content that only one parser captured

Design intent: offline batch analysis for corpus-scale comparison. The
audit does NOT run parsers itself — the user provides pre-parsed
outputs. This keeps the audit lightweight (no marker/docling/markitdown
dependency required) and makes it usable against any parser, present
or future.

CLI entry point: ``aksharamd audit`` (see aksharamd/cli.py).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _tokenize(text: str) -> list[str]:
    """Return lowercase alphanumeric tokens from `text`. Case-normalized so
    Jaccard similarity treats 'Revenue' and 'revenue' as the same word."""
    return [w.lower() for w in _WORD_RE.findall(text)]


def _extract_pdf_text(pdf_bytes: bytes) -> tuple[str, int]:
    """Return (concatenated_text, page_count). Never raises."""
    if not pdf_bytes:
        return "", 0
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return "", 0
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            page_count = len(pdf)
            texts = [page.get_text("text") or "" for page in pdf]
        return "\n".join(texts), page_count
    except Exception:  # noqa: BLE001 — any PyMuPDF failure -> skip
        return "", 0


@dataclass(frozen=True)
class ParserMetric:
    """Per-parser word-count metrics against the source PDF."""

    parser_id: str
    markdown_word_count: int
    markdown_unique_word_count: int
    fidelity_ratio: float           # markdown_word_count / pdf_word_count
    pdf_overlap_ratio: float        # |md_words ∩ pdf_words| / |pdf_words|
    unique_vs_others_count: int     # words only this parser captured

    def to_dict(self) -> dict:
        return {
            "parser_id": self.parser_id,
            "markdown_word_count": self.markdown_word_count,
            "markdown_unique_word_count": self.markdown_unique_word_count,
            "fidelity_ratio": round(self.fidelity_ratio, 4),
            "pdf_overlap_ratio": round(self.pdf_overlap_ratio, 4),
            "unique_vs_others_count": self.unique_vs_others_count,
        }


@dataclass(frozen=True)
class AuditResult:
    """Full audit output — per-parser metrics + pairwise similarity matrix."""

    source_path: str
    pdf_page_count: int
    pdf_word_count: int
    parsers: tuple[ParserMetric, ...] = field(default_factory=tuple)
    # jaccard_matrix[i][j] = |set(parsers[i]) ∩ set(parsers[j])| / |union|
    jaccard_matrix: tuple[tuple[float, ...], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "pdf_page_count": self.pdf_page_count,
            "pdf_word_count": self.pdf_word_count,
            "parsers": [p.to_dict() for p in self.parsers],
            "jaccard_matrix": [
                [round(v, 4) for v in row] for row in self.jaccard_matrix
            ],
        }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def run_audit(
    source_pdf: Path,
    parsed_files: dict[str, Path],
) -> AuditResult:
    """Run the consensus audit on a source PDF and its parsed variants.

    ``parsed_files`` maps parser_id -> path to that parser's markdown
    output. Missing paths raise FileNotFoundError; malformed PDFs
    produce a result with pdf_word_count == 0 and best-effort per-parser
    metrics against an empty PDF set.
    """
    source_path = Path(source_pdf)
    if not source_path.is_file():
        raise FileNotFoundError(f"source PDF not found: {source_path}")

    pdf_text, pdf_page_count = _extract_pdf_text(source_path.read_bytes())
    pdf_tokens = _tokenize(pdf_text)
    pdf_word_count = len(pdf_tokens)
    pdf_word_set: set[str] = set(pdf_tokens)

    # Per-parser word sets (used both for per-parser metrics and pairwise).
    parser_word_sets: dict[str, set[str]] = {}
    parser_word_counts: dict[str, int] = {}
    for parser_id, path in parsed_files.items():
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"parsed file for {parser_id!r} not found: {p}")
        tokens = _tokenize(p.read_text(encoding="utf-8"))
        parser_word_sets[parser_id] = set(tokens)
        parser_word_counts[parser_id] = len(tokens)

    ordered_ids = list(parsed_files.keys())

    metrics: list[ParserMetric] = []
    for parser_id in ordered_ids:
        ws = parser_word_sets[parser_id]
        md_count = parser_word_counts[parser_id]
        # unique_vs_others = words this parser found that no other parser found
        others_union: set[str] = set()
        for other_id, other_ws in parser_word_sets.items():
            if other_id != parser_id:
                others_union |= other_ws
        unique_vs_others = ws - others_union
        metrics.append(ParserMetric(
            parser_id=parser_id,
            markdown_word_count=md_count,
            markdown_unique_word_count=len(ws),
            fidelity_ratio=(md_count / pdf_word_count) if pdf_word_count > 0 else 0.0,
            pdf_overlap_ratio=(
                len(ws & pdf_word_set) / len(pdf_word_set)
                if pdf_word_set
                else 0.0
            ),
            unique_vs_others_count=len(unique_vs_others),
        ))

    matrix: list[tuple[float, ...]] = []
    for a_id in ordered_ids:
        row: list[float] = []
        for b_id in ordered_ids:
            row.append(_jaccard(parser_word_sets[a_id], parser_word_sets[b_id]))
        matrix.append(tuple(row))

    return AuditResult(
        source_path=str(source_path),
        pdf_page_count=pdf_page_count,
        pdf_word_count=pdf_word_count,
        parsers=tuple(metrics),
        jaccard_matrix=tuple(matrix),
    )


def format_report(result: AuditResult) -> str:
    """Human-readable report — plain text, no ANSI, suitable for logs."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"Consensus audit: {result.source_path}")
    lines.append(
        f"Source PDF: {result.pdf_page_count} page(s), "
        f"{result.pdf_word_count} words extracted"
    )
    lines.append("")

    lines.append("Per-parser metrics:")
    lines.append(
        f"  {'parser':<20} {'words':>8} {'unique':>8} "
        f"{'fidelity':>10} {'pdf-overlap':>12} {'only-here':>10}"
    )
    for m in result.parsers:
        lines.append(
            f"  {m.parser_id:<20} "
            f"{m.markdown_word_count:>8} "
            f"{m.markdown_unique_word_count:>8} "
            f"{m.fidelity_ratio:>10.1%} "
            f"{m.pdf_overlap_ratio:>12.1%} "
            f"{m.unique_vs_others_count:>10}"
        )
    lines.append("")

    lines.append("Pairwise Jaccard word-set similarity:")
    ids = [m.parser_id for m in result.parsers]
    header = "  " + " " * 20 + "".join(f"{i[:10]:>12}" for i in ids)
    lines.append(header)
    for i, row in enumerate(result.jaccard_matrix):
        row_str = "  " + f"{ids[i]:<20}"
        for v in row:
            row_str += f"{v:>12.3f}"
        lines.append(row_str)

    lines.append("")
    lines.append(
        "Interpretation: fidelity < 0.5 flags possible dropped content "
        "(W_DROPPED_CONTENT threshold). pdf-overlap measures how much of "
        "the source PDF's vocabulary each parser captured. Jaccard values "
        "close to 1.0 mean parsers agree; values below 0.6 flag "
        "disagreement zones worth manual review."
    )
    lines.append("=" * 72)
    return "\n".join(lines)


def format_json(result: AuditResult) -> str:
    """Machine-readable JSON — one-line-per-parser for grep-friendly diffs."""
    return json.dumps(result.to_dict(), indent=2, sort_keys=True)
