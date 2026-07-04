"""Shared utilities used across the AksharaMD pipeline."""
from __future__ import annotations

import re as _re

# ── Token counting ─────────────────────────────────────────────────────────────


def _count_tokens_fallback(text: str) -> int:
    """Count tokens for text without tiktoken.

    For CJK scripts where words are not space-separated, each character is
    counted as an individual token in addition to whitespace-split words.
    """
    cjk = len(_re.findall(r'[一-鿿぀-ヿ가-힯]', text))
    words = len(text.split())
    return words + cjk  # CJK chars count as individual tokens on top of word splits


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken cl100k_base. Falls back to whitespace split."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, _count_tokens_fallback(text))


# ── Model pricing ──────────────────────────────────────────────────────────────
# Price per 1M *input* tokens (USD). Updated June 2026.
# These are the rates a user would pay when feeding a document into the model.

TOKEN_PRICES: dict[str, float] = {
    "gpt-4o":              2.50,
    "gpt-4o-mini":         0.15,
    "claude-sonnet-4":     3.00,
    "claude-haiku-4":      0.80,
    "claude-opus-4":      15.00,
    "gemini-1.5-pro":      3.50,
    "gemini-1.5-flash":    0.075,
}

# Which models to show in the CLI (ordered by popularity)
DISPLAY_MODELS = ["gpt-4o", "claude-sonnet-4", "gpt-4o-mini", "claude-haiku-4"]


def tokens_to_dollars(tokens: int, model: str) -> float:
    """Convert a token count to USD cost for a given model."""
    price_per_million = TOKEN_PRICES.get(model, TOKEN_PRICES["gpt-4o"])
    return tokens * price_per_million / 1_000_000


def format_savings_line(tokens_saved: int) -> str:
    """Single-line dollar savings across display models, e.g. '$0.20 · $0.24 · $0.01'"""
    parts = []
    for model in DISPLAY_MODELS:
        dollars = tokens_to_dollars(tokens_saved, model)
        parts.append(f"${dollars:.3f} ({model})")
    return "  |  ".join(parts)
