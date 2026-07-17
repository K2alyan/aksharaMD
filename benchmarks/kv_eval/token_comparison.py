"""Compare token counts before and after KV promotion."""
from __future__ import annotations

from pydantic import BaseModel


class TokenComparison(BaseModel):
    case_id: str
    source_text_tokens: int
    markdown_list_tokens: int
    tsv_tokens: int
    selected_tokens: int
    selected_format: str = "markdown"   # "markdown" or "tsv"
    delta_vs_source: int        # selected - source (negative = savings)
    delta_pct: float            # delta / source * 100

def compare_tokens(case_id: str, source_text: str, group) -> TokenComparison:
    from aksharamd.packaging.token_accounting import count_text_tokens
    from aksharamd.renderers.key_value_markdown import render_key_value_group, render_key_value_tsv

    src = count_text_tokens(source_text)
    md = render_key_value_group(group)
    tsv = render_key_value_tsv(group)
    md_tok = count_text_tokens(md)
    tsv_tok = count_text_tokens(tsv)
    # Token-aware: always pick smaller
    selected = tsv_tok if tsv_tok < md_tok else md_tok
    selected_format = "tsv" if tsv_tok < md_tok else "markdown"
    delta = selected - src
    return TokenComparison(
        case_id=case_id,
        source_text_tokens=src,
        markdown_list_tokens=md_tok,
        tsv_tokens=tsv_tok,
        selected_tokens=selected,
        selected_format=selected_format,
        delta_vs_source=delta,
        delta_pct=round(delta / src * 100, 1) if src > 0 else 0.0,
    )
