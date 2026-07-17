"""Post-parse key-value group promotion.

Runs after clean, before optimize. Replaces qualifying paragraph blocks
with KeyValueGroup blocks. Conservative: only promotes when evidence is strong.

Detector version: "kv_promoter/v2"

kv_promoter/v2
--------------
- Reads a KeyValueDetectionProfile from ctx.kv_profile (or default profile
  when unset).
- Heuristic paths (inline, adjacent) are OFF by default. Round 1 hard
  negatives measured FPR=0.929 for the inline heuristic; heuristics now
  require an explicit opt-in via KeyValueDetectionProfile.experimental().
- When heuristics are off but ``emit_candidate_diagnostics`` is on, the
  promoter still runs a diagnostic pass that emits
  W_KEY_VALUE_STRUCTURE_POSSIBLE for candidate paragraphs. It never mutates
  blocks in this mode.
- The adjacent-block promoter now has a Strategy 2 pass that recognises
  alternating key-only / value-only paragraph pairs, feeding the resulting
  virtual text back through the inline detector so exclusions / positive
  evidence still apply.
"""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...context import CompilationContext
    from ...scoring.key_value_config import KeyValueDetectionProfile

logger = logging.getLogger(__name__)

DETECTOR_VERSION = "kv_promoter/v2"

# Maximum lines in a paragraph block to attempt KV detection
_MAX_PARA_LINES = 20

# Maximum chars in a paragraph for inline KV detection
_MAX_PARA_CHARS = 600


def detect_and_promote_key_value_groups(
    ctx: "CompilationContext",
    profile: "KeyValueDetectionProfile | None" = None,
) -> "CompilationContext":
    """Walk document blocks and promote qualifying regions to KeyValueGroup blocks.

    Returns ctx with modified document.blocks. Non-mutating: creates new block
    list.

    If ``profile`` is None the profile is taken from ``ctx.kv_profile`` if set,
    otherwise defaults to KeyValueDetectionProfile() (native-only).
    """
    if ctx.document is None:
        return ctx

    blocks = ctx.document.blocks
    if not blocks:
        return ctx

    if profile is None:
        from ...scoring.key_value_config import KeyValueDetectionProfile
        profile = getattr(ctx, "kv_profile", None) or KeyValueDetectionProfile()

    new_blocks = _promote_blocks(blocks, ctx, profile)

    if len(new_blocks) != len(blocks) or any(
        nb.id != ob.id for nb, ob in zip(new_blocks, blocks)
    ):
        ctx.document = ctx.document.model_copy(update={"blocks": new_blocks})

    return ctx


def _promote_blocks(blocks, ctx, profile) -> list:
    """Walk blocks and apply promotion passes based on the active profile."""
    result = blocks
    if profile.enable_inline_heuristic:
        result = _pass_inline(result, ctx, profile)
    elif profile.emit_candidate_diagnostics:
        result = _pass_inline_diagnostic_only(result, ctx, profile)

    if profile.enable_adjacent_heuristic:
        result = _pass_adjacent(result, ctx, profile)

    return result


def _pass_inline(blocks, ctx, profile) -> list:
    """Replace paragraph blocks whose text is 2+ Key: Value lines."""
    from ...models.block import BlockType

    result = []
    for block in blocks:
        if (
            block.type == BlockType.PARAGRAPH
            and block.content
            and len(block.content) <= _MAX_PARA_CHARS
            and "\n" in block.content
            and ":" in block.content
        ):
            promoted = _try_promote_paragraph(block, ctx, profile)
            if promoted is not None:
                result.append(promoted)
                continue
        result.append(block)
    return result


def _pass_inline_diagnostic_only(blocks, ctx, profile) -> list:
    """Detect candidate paragraphs without promoting.

    Used when the inline heuristic is disabled but callers still want
    W_KEY_VALUE_STRUCTURE_POSSIBLE diagnostics for downstream review.
    """
    from ...models.block import BlockType

    for block in blocks:
        if (
            block.type == BlockType.PARAGRAPH
            and block.content
            and len(block.content) <= _MAX_PARA_CHARS
            and "\n" in block.content
            and ":" in block.content
        ):
            _try_emit_candidate_diagnostic(block, ctx, profile)
    return blocks


def _try_emit_candidate_diagnostic(block, ctx, profile) -> None:
    """Run the detector purely for signal emission — never mutates blocks."""
    from ...scoring.key_value_detection import detect_key_value_entries

    lines = [ln.strip() for ln in block.content.splitlines() if ln.strip()]
    if len(lines) < 2 or len(lines) > _MAX_PARA_LINES:
        return

    # Use the calling profile so the assessment reflects the same
    # exclusion configuration the promoter would use.
    diag_result = detect_key_value_entries(
        block.content, page=block.page, profile=profile
    )
    if diag_result.assessment is None:
        return
    if diag_result.assessment.candidate_entries < 2:
        return
    if diag_result.assessment.exclusion_categories:
        return
    if diag_result.assessment.promotion_decision != "promote":
        # We emit the diagnostic even when the classifier would not have
        # promoted, as long as no exclusion fired — the promoter is off,
        # so the diagnostic is the only signal available.
        pass

    signals = list(diag_result.assessment.exclusion_categories) or ["candidate_only"]
    _emit_kv_diagnostic(
        ctx,
        block,
        signals,
        diag_result.rejected_reason or "heuristic_disabled",
    )


def _try_promote_paragraph(block, ctx, profile) -> "object | None":
    """Attempt to promote a single paragraph to a KeyValueGroup."""
    from ...scoring.key_value_detection import detect_key_value_entries

    lines = [ln.strip() for ln in block.content.splitlines() if ln.strip()]
    if len(lines) < 2 or len(lines) > _MAX_PARA_LINES:
        return None

    result = detect_key_value_entries(
        block.content, page=block.page, profile=profile
    )

    if result.group is None:
        if result.signals or result.assessment is not None:
            reason = result.rejected_reason or ""
            _emit_kv_diagnostic(ctx, block, result.signals, reason)
        return None

    group = result.group
    group = group.model_copy(update={"source_block_ids": [block.id]})

    return _build_kv_block(group, block, [block], "kv_inline_detection")


def _pass_adjacent(blocks, ctx, profile) -> list:
    """Detect adjacent-block KV patterns."""
    from ...models.block import BlockType

    result: list = []
    i = 0

    while i < len(blocks):
        block = blocks[i]

        if block.type != BlockType.PARAGRAPH:
            result.append(block)
            i += 1
            continue

        run, end_i = _collect_adjacent_run(blocks, i)

        if run is not None and len(run) >= 4:
            promoted = _try_promote_adjacent_run(run, ctx, profile)
            if promoted is not None:
                result.append(promoted)
                i = end_i
                continue

        result.append(block)
        i += 1

    return result


def _collect_adjacent_run(blocks, start_i: int) -> tuple:
    """Collect a run of adjacent short paragraph blocks that could be KV pairs."""
    from ...models.block import BlockType

    _STOP_TYPES = frozenset({
        BlockType.HEADING, BlockType.TABLE, BlockType.IMAGE,
        BlockType.PAGE_BREAK, BlockType.KEY_VALUE_GROUP,
    })

    run = []
    i = start_i
    first_page = blocks[start_i].page

    while i < len(blocks):
        b = blocks[i]

        if b.type in _STOP_TYPES:
            break

        if b.type != BlockType.PARAGRAPH:
            break

        if first_page is not None and b.page is not None:
            if abs((b.page or 0) - (first_page or 0)) > 1:
                break

        content = (b.content or "").strip()

        if len(content) > 100 and ":" not in content:
            break
        if not content:
            break

        run.append(b)
        i += 1
        if len(run) >= 20:
            break

    if len(run) >= 2:
        return run, i
    return None, start_i + 1


def _try_promote_adjacent_run(run, ctx, profile) -> "object | None":
    """Attempt to promote a run of adjacent blocks to a KeyValueGroup.

    Two strategies are tried in order:

    1. Join all block content with newlines and run the inline detector.
       This works when each block already contains "Key: Value".
    2. Look for alternating key-only / value-only paragraphs; synthesise a
       virtual "Key: Value" text and feed that through the inline detector.

    Both strategies pass through the same v2 classifier so exclusions and
    positive-evidence rules apply uniformly.
    """
    from ...scoring.key_value_detection import detect_key_value_entries

    first_page = next((b.page for b in run if b.page is not None), None)

    # Strategy 1: standard joined text.
    combined_text = "\n".join((b.content or "").strip() for b in run)
    result = detect_key_value_entries(
        combined_text, page=first_page, profile=profile
    )
    if result.group is not None:
        group = result.group
        source_ids = [b.id for b in run]
        group = group.model_copy(update={"source_block_ids": source_ids})
        return _build_kv_block(group, run[0], run, "kv_adjacent_detection")

    # Strategy 2: alternating key-only / value-only blocks.
    alt_candidates = _parse_alternating_blocks(run)
    if len(alt_candidates) >= 2:
        virtual_text = "\n".join(
            f"{c.key}: {c.value}" for c in alt_candidates
        )
        result2 = detect_key_value_entries(
            virtual_text, page=first_page, profile=profile
        )
        if result2.group is not None:
            group = result2.group
            source_ids = [b.id for b in run]
            group = group.model_copy(update={"source_block_ids": source_ids})
            return _build_kv_block(
                group, run[0], run, "kv_adjacent_alternating_detection"
            )

    return None


def _parse_alternating_blocks(run) -> list:
    """Extract key/value pairs from alternating key-only / value-only blocks.

    Looks for consecutive pairs:
      Block[i]:   "SomeLabel:"   (ends with colon, no value on same line)
      Block[i+1]: "some value"   (no colon, plausible value)
    """
    from ...scoring.key_value_detection import (
        _MAX_LABEL_WORDS,
        _MAX_VALUE_CHARS,
        _RHETORICAL_LABELS,
        KeyValueCandidate,
    )

    candidates: list = []
    i = 0
    while i < len(run):
        key_text = (run[i].content or "").strip()
        if key_text.endswith(":") and len(key_text) > 1:
            key = key_text[:-1].strip()
            if (
                key
                and len(key.split()) <= _MAX_LABEL_WORDS
                and key.lower() not in _RHETORICAL_LABELS
                and i + 1 < len(run)
            ):
                val_text = (run[i + 1].content or "").strip()
                # Value must not look like another key/value line — i.e.,
                # it must not contain ": " or end with ":". Bare colons
                # (e.g. "9:00 AM") are fine.
                looks_like_kv = ": " in val_text or val_text.endswith(":")
                if (
                    val_text
                    and not looks_like_kv
                    and len(val_text) <= _MAX_VALUE_CHARS
                ):
                    candidates.append(
                        KeyValueCandidate(
                            key=key,
                            value=val_text,
                            line=f"{key}: {val_text}",
                        )
                    )
                    i += 2
                    continue
        i += 1
    return candidates


def _build_kv_block(group, first_block, source_blocks, transformation: str):
    """Build a KEY_VALUE_GROUP block from a KeyValueGroup, preserving provenance."""
    from ...models.block import Block, ExtractionConfidence

    original_texts = "\n".join((b.content or "") for b in source_blocks)
    orig_checksum = hashlib.sha256(original_texts.encode()).hexdigest()[:16]

    meta = {
        "transformation": transformation,
        "source_block_ids": [b.id for b in source_blocks],
        "detector_version": DETECTOR_VERSION,
        "original_text_checksum": orig_checksum,
        "group_type": str(group.group_type),
        "entry_count": len(group.entries),
    }

    return Block.from_key_value_group(
        group,
        page=first_block.page,
        index=first_block.index,
        confidence=ExtractionConfidence.INFERRED,
        metadata=meta,
    )


def _emit_kv_diagnostic(ctx, block, signals: list, reason: str) -> None:
    """Emit W_KEY_VALUE_STRUCTURE_POSSIBLE for medium-confidence cases."""
    try:
        from ...models.validation import Severity, ValidationIssue
        issue = ValidationIssue(
            severity=Severity.WARNING,
            code="W_KEY_VALUE_STRUCTURE_POSSIBLE",
            message=(
                f"Block on page {block.page} may contain key-value structure "
                f"(signals: {', '.join(signals)}; rejected: {reason})"
            ),
            block_id=block.id,
            metadata={
                "signals": signals,
                "rejected_reason": reason,
                "page": block.page,
                "detector_version": DETECTOR_VERSION,
                "maturity": "experimental",
                "penalty": 0,
            },
        )
        ctx.validation.issues.append(issue)
    except Exception:  # noqa: BLE001 - diagnostic is best-effort
        pass
