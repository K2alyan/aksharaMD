"""
Multi-Granularity Adaptive Matching (MGAM) scoring for document parser evaluation.

Based on the OmniDocBench evaluation methodology (CVPR 2025):
  "Instead of single-block matching, MGAM merges consecutive prediction blocks
   until the similarity to the reference block stops improving."

This gives a truer recall score than exact-match rules because content that is
correctly extracted but chunked differently (e.g. across a page break, or split
by a table) still scores well.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# ── Text normalisation ────────────────────────────────────────────────────────

_BOLD_ITALIC_RE = re.compile(r"\*{1,3}(.+?)\*{1,3}", re.DOTALL)
_UNDERLINE_RE = re.compile(r"<u>(.+?)</u>", re.DOTALL)
_STRIKETHROUGH_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_SUP_RE = re.compile(r"<sup>(.+?)</sup>", re.DOTALL)
_SUB_RE = re.compile(r"<sub>(.+?)</sub>", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_TABLE_PIPE_RE = re.compile(r"\|")
_TABLE_SEP_RE = re.compile(r"^[\s|:\-]+$", re.MULTILINE)
_ASSET_RE = re.compile(r"!\[.*?\]\(asset://[a-f0-9]+\)")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Strip markdown syntax and collapse whitespace for similarity comparison."""
    text = _BOLD_ITALIC_RE.sub(r"\1", text)
    text = _UNDERLINE_RE.sub(r"\1", text)
    text = _STRIKETHROUGH_RE.sub(r"\1", text)
    text = _SUP_RE.sub(r"\1", text)
    text = _SUB_RE.sub(r"\1", text)
    text = _HEADING_RE.sub("", text)
    text = _ASSET_RE.sub("", text)       # drop image asset references
    text = _TABLE_SEP_RE.sub(" ", text)  # table divider rows → space
    text = _TABLE_PIPE_RE.sub(" ", text) # pipe chars → space
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# ── Reference block extraction from raw PyMuPDF text ─────────────────────────

_MIN_BLOCK_CHARS = 20    # shorter blocks are probably page numbers / noise
_MAX_NUMERIC_RATIO = 0.6 # blocks >60% digits are likely tables/line numbers


def _is_reference_noise(text: str) -> bool:
    text = text.strip()
    if len(text) < _MIN_BLOCK_CHARS:
        return True
    digits = sum(1 for c in text if c.isdigit())
    if len(text) > 0 and digits / len(text) > _MAX_NUMERIC_RATIO:
        return True
    return False


def extract_reference_blocks(raw_text: str) -> list[str]:
    """Split raw PDF text into reference blocks, filtering noise.

    Paragraphs are separated by two or more newlines.  Short runs (page numbers,
    headers, table column fragments) are discarded so that AksharaMD is not
    penalised for intentionally stripping noise.
    """
    raw_blocks = re.split(r"\n{2,}", raw_text)
    blocks = []
    for blk in raw_blocks:
        blk = _WHITESPACE_RE.sub(" ", blk).strip()
        if blk and not _is_reference_noise(blk):
            blocks.append(blk)
    return blocks


# ── Similarity ────────────────────────────────────────────────────────────────

def _sim(a: str, b: str) -> float:
    """Character-level similarity in [0, 1].  Uses SequenceMatcher (stdlib LCS ratio)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ── MGAM core ─────────────────────────────────────────────────────────────────

_IMPROVE_EPSILON = 1e-4  # stop merging when gain is below this


def _best_match_score(
    query: str,
    pool: list[str],
    max_merge: int,
) -> float:
    """For a single query block, find the highest similarity across all windows
    in pool, where a window is a contiguous run of 1..max_merge pool blocks
    merged with a space separator.

    The adaptive stopping rule (Sec. 3.3 of OmniDocBench): expand the window
    from each starting position until similarity stops improving.
    """
    best = 0.0
    n = len(pool)
    for j in range(n):
        merged = pool[j]
        score = _sim(query, merged)
        if score > best:
            best = score
        for k in range(j + 1, min(j + max_merge, n)):
            extended = merged + " " + pool[k]
            new_score = _sim(query, extended)
            if new_score > score + _IMPROVE_EPSILON:
                score = new_score
                merged = extended
                if score > best:
                    best = score
            else:
                break  # merging further won't help
    return best


@dataclass
class MGAMResult:
    recall: float       # fraction of reference content found in prediction
    precision: float    # fraction of prediction content found in reference
    f1: float
    per_ref_scores: list[float]   # per reference-block match scores
    per_pred_scores: list[float]  # per prediction-block match scores


def mgam_score(
    ref_blocks: list[str],
    pred_blocks: list[str],
    max_merge: int = 8,
) -> MGAMResult:
    """Compute MGAM recall, precision, and F1 between two sets of text blocks.

    ref_blocks:  reference document content, one item per block
    pred_blocks: parser output content, one item per block (markdown stripped)
    max_merge:   maximum number of consecutive blocks to merge when searching
                 for the best match (8 is generous; diminishing returns above 5)
    """
    ref_norm = [normalize(b) for b in ref_blocks if b.strip()]
    pred_norm = [normalize(b) for b in pred_blocks if b.strip()]

    if not ref_norm:
        return MGAMResult(1.0, 1.0, 1.0, [], [])
    if not pred_norm:
        return MGAMResult(0.0, 0.0, 0.0, [0.0] * len(ref_norm), [])

    per_ref = [_best_match_score(r, pred_norm, max_merge) for r in ref_norm]
    per_pred = [_best_match_score(p, ref_norm, max_merge) for p in pred_norm]

    recall = sum(per_ref) / len(per_ref)
    precision = sum(per_pred) / len(per_pred)
    f1 = (2 * recall * precision / (recall + precision)) if (recall + precision) > 0 else 0.0

    return MGAMResult(
        recall=recall,
        precision=precision,
        f1=f1,
        per_ref_scores=per_ref,
        per_pred_scores=per_pred,
    )
