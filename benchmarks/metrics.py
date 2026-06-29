from __future__ import annotations
import re
from dataclasses import dataclass, field

_NOISE_RE = re.compile(r"^.{1,3}$")          # lines 1-3 chars long
_PAGE_NUM_RE = re.compile(r"^\d+$|^page\s+\d+", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6}\s+")


@dataclass
class DocumentMetrics:
    tool: str
    source: str
    elapsed_seconds: float = 0.0
    token_count: int = 0
    char_count: int = 0
    line_count: int = 0
    heading_count: int = 0
    table_count: int = 0
    noise_line_count: int = 0       # lines that are just 1-3 chars or page numbers
    duplicate_line_count: int = 0   # repeated lines
    empty_line_count: int = 0
    avg_paragraph_tokens: float = 0.0
    quality_score: int = 0          # 0-100: content density + coherence
    errors: list[str] = field(default_factory=list)


_CODE_FENCE_RE = re.compile(r"^```")


def _compute_quality_score(
    token_count: int,
    line_count: int,
    noise_line_count: int,
    duplicate_line_count: int,
    empty_line_count: int,
    avg_paragraph_tokens: float,
) -> int:
    """Score content quality 0-100 without ground truth.

    Two components:
      - Signal-to-noise (60 pts): penalises noise and duplicate lines
      - Paragraph coherence (40 pts): rewards avg paragraph length in 20-300 token range
    """
    if token_count == 0:
        return 0

    content_lines = max(1, line_count - empty_line_count)

    # Signal-to-noise: noise + duplicates reduce signal
    bad_lines = noise_line_count + duplicate_line_count
    bad_ratio = min(1.0, bad_lines / content_lines)
    sn_pts = int((1 - bad_ratio) * 60)

    # Paragraph coherence: optimal is 20-300 tokens per paragraph
    apt = avg_paragraph_tokens
    if apt == 0:
        # All content in tables/headings; treat as 50% coherent
        coh = 0.5
    elif apt < 5:
        coh = 0.15
    elif apt < 20:
        coh = 0.15 + (apt - 5) / 15 * 0.85   # ramp 0.15 → 1.0
    elif apt <= 300:
        coh = 1.0
    elif apt <= 800:
        coh = 1.0 - (apt - 300) / 1000        # gentle slope down
    else:
        coh = 0.5

    coh_pts = int(coh * 40)
    return min(100, max(0, sn_pts + coh_pts))


def compute_metrics(tool: str, source: str, text: str, elapsed: float) -> DocumentMetrics:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        token_count = len(enc.encode(text))
    except Exception:
        token_count = max(1, len(text.split()))

    lines = text.splitlines()
    seen: set[str] = set()
    dup_count = 0
    noise_count = 0
    heading_count = 0
    table_count = 0
    empty_count = 0
    para_tokens: list[int] = []
    current_para: list[str] = []
    in_code_fence = False

    def flush_para():
        if current_para:
            t = " ".join(current_para)
            try:
                import tiktoken
                enc = tiktoken.get_encoding("cl100k_base")
                para_tokens.append(len(enc.encode(t)))
            except Exception:
                para_tokens.append(len(t.split()))
            current_para.clear()

    for line in lines:
        stripped = line.strip()

        if _CODE_FENCE_RE.match(stripped):
            in_code_fence = not in_code_fence
            flush_para()
            continue

        if in_code_fence:
            continue

        if not stripped:
            empty_count += 1
            flush_para()
            continue

        if stripped in seen:
            dup_count += 1
        seen.add(stripped)

        if _NOISE_RE.match(stripped) or _PAGE_NUM_RE.match(stripped):
            noise_count += 1
            continue

        if _HEADING_RE.match(stripped):
            heading_count += 1
            flush_para()
        elif stripped.startswith("|"):
            table_count += 1
            flush_para()
        else:
            current_para.append(stripped)

    flush_para()

    avg_pt = round(sum(para_tokens) / len(para_tokens), 1) if para_tokens else 0.0

    quality = _compute_quality_score(
        token_count=token_count,
        line_count=len(lines),
        noise_line_count=noise_count,
        duplicate_line_count=dup_count,
        empty_line_count=empty_count,
        avg_paragraph_tokens=avg_pt,
    )

    return DocumentMetrics(
        tool=tool,
        source=source,
        elapsed_seconds=round(elapsed, 3),
        token_count=token_count,
        char_count=len(text),
        line_count=len(lines),
        heading_count=heading_count,
        table_count=table_count,
        noise_line_count=noise_count,
        duplicate_line_count=dup_count,
        empty_line_count=empty_count,
        avg_paragraph_tokens=avg_pt,
        quality_score=quality,
    )
