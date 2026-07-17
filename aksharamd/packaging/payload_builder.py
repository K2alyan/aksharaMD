"""Build an LLM payload from a finalized DocumentPackagePlan."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from .models import (
    PackageAssetReference,
    PackageProfile,
    PackageSourceKind,
    ReasonCode,
    RelationshipType,
    RepresentationType,
)
from .payload import (
    LLMPayload,
    LLMPayloadItem,
    PayloadContentType,
    PayloadFidelity,
    TokenDeltaBreakdown,
)
from .token_accounting import count_text_tokens

if TYPE_CHECKING:
    from ..models.document import Document
    from .models import DocumentPackagePlan, PackageElementPlan


def build_table_candidates(
    table_data,
    table_id: str,
    artifact_path: str | None,
    block_content_tokens: int,
    profile: PackageProfile,
    title: str | None = None,
) -> list:
    """Generate all candidate serializations for one table."""
    from ..renderers.table_markdown import (
        render_table_json_reference,
        render_table_markdown,
        render_table_preview_reference,
        render_table_row_records,
        render_table_tsv,
    )
    from .models import TablePayloadFormat, TableSerializationCandidate
    from .token_accounting import count_text_tokens

    header_count = len(table_data.header_rows) if table_data.header_rows else 0
    body_rows = max(0, table_data.row_count - header_count)

    candidates = []

    # 1. Markdown
    md = render_table_markdown(table_data)
    candidates.append(TableSerializationCandidate(
        format=TablePayloadFormat.MARKDOWN,
        text=md,
        token_count=count_text_tokens(md),
        preserves_all_rows_inline=True,
        preserves_structure_inline=True,
        artifact_path=artifact_path,
        omitted_row_count=0,
    ))

    # 2. TSV
    tsv = render_table_tsv(table_data)
    candidates.append(TableSerializationCandidate(
        format=TablePayloadFormat.TSV,
        text=tsv,
        token_count=count_text_tokens(tsv),
        preserves_all_rows_inline=True,
        preserves_structure_inline=True,
        artifact_path=artifact_path,
        omitted_row_count=0,
    ))

    # 3. Row records (only if headers available and narrow enough)
    rr = render_table_row_records(table_data)
    if rr:
        candidates.append(TableSerializationCandidate(
            format=TablePayloadFormat.ROW_RECORDS,
            text=rr,
            token_count=count_text_tokens(rr),
            preserves_all_rows_inline=True,
            preserves_structure_inline=True,
            artifact_path=artifact_path,
            omitted_row_count=0,
        ))

    preview_rows_n = getattr(profile, "table_preview_rows", 5)

    # 4. Preview + reference
    if getattr(profile, "allow_table_artifact_references", True):
        pr = render_table_preview_reference(
            table_data, table_id, artifact_path, preview_rows=preview_rows_n, title=title
        )
        omitted = max(0, body_rows - preview_rows_n)
        candidates.append(TableSerializationCandidate(
            format=TablePayloadFormat.PREVIEW_REFERENCE,
            text=pr,
            token_count=count_text_tokens(pr),
            preserves_all_rows_inline=(omitted == 0),
            preserves_structure_inline=True,
            artifact_path=artifact_path,
            omitted_row_count=omitted,
        ))

        # 5. JSON reference
        jr = render_table_json_reference(table_data, table_id, artifact_path)
        candidates.append(TableSerializationCandidate(
            format=TablePayloadFormat.JSON_REFERENCE,
            text=jr,
            token_count=count_text_tokens(jr),
            preserves_all_rows_inline=False,
            preserves_structure_inline=False,
            artifact_path=artifact_path,
            omitted_row_count=body_rows,
        ))

    return candidates


def select_table_serialization(
    candidates: list,
    mode: str,
    profile: PackageProfile,
    block_content_tokens: int,
) -> object:
    """Choose the best serialization candidate for the given mode.

    Regression guard: a full-inline candidate must not exceed
    block_content_tokens * 1.05. If it does, fall back to preview_reference
    (unless strategy == "full_inline").

    Selection policy:
    - "auto" strategy:
      - text_first: lowest-token full-inline that passes the guard;
        if none pass, use preview_reference; if unavailable, json_reference.
      - adaptive: lowest-token full-inline that passes the guard;
        large tables (>max_inline_table_tokens) use preview_reference.
      - fidelity_first: lowest-token full-inline (ignores guard unless
        strategy != "full_inline" and tokens are extreme); otherwise preview_reference.
    - "full_inline": always pick lowest-token full-inline regardless of guard.
    - "preview_reference": always pick preview_reference; fall back to json_reference.
    - "reference_only": always pick json_reference.

    max_inline_table_tokens: if full-inline token_count > this, prefer preview over full-inline
    in text_first and adaptive modes.
    """
    from .models import TablePayloadFormat, TableSerializationCandidate

    strategy = getattr(profile, "table_payload_strategy", "auto")
    max_inline = getattr(profile, "max_inline_table_tokens", 1200)
    GUARD_FACTOR = 1.05

    full_inline = [c for c in candidates if c.preserves_all_rows_inline]
    preview_ref = next((c for c in candidates if c.format == TablePayloadFormat.PREVIEW_REFERENCE), None)
    json_ref = next((c for c in candidates if c.format == TablePayloadFormat.JSON_REFERENCE), None)
    fallback = json_ref or (candidates[-1] if candidates else None)

    if not candidates:
        # Should never happen; return a placeholder
        return TableSerializationCandidate(
            format=TablePayloadFormat.MARKDOWN, text="", token_count=0,
            preserves_all_rows_inline=True, preserves_structure_inline=True,
        )

    if strategy == "reference_only":
        return json_ref or fallback

    if strategy == "preview_reference":
        return preview_ref or json_ref or fallback

    # For "full_inline" and "auto": sort full-inline by token count
    full_inline_sorted = sorted(full_inline, key=lambda c: c.token_count)
    best_full_inline = full_inline_sorted[0] if full_inline_sorted else None

    if strategy == "full_inline":
        return best_full_inline or fallback

    # "auto" strategy — mode-differentiated
    def passes_guard(c):
        if block_content_tokens <= 0:
            return True
        return c.token_count <= block_content_tokens * GUARD_FACTOR

    def within_budget(c):
        return c.token_count <= max_inline

    if mode == "text_first":
        # Lowest-token full-inline that passes guard AND budget
        eligible = [c for c in full_inline_sorted if passes_guard(c) and within_budget(c)]
        if eligible:
            return eligible[0]
        # Fall to preview_reference, then json_reference
        return preview_ref or json_ref or fallback

    elif mode == "adaptive":
        # Same as text_first but: if table fits in budget, prefer full-inline even if guard fail is marginal
        eligible = [c for c in full_inline_sorted if passes_guard(c) and within_budget(c)]
        if eligible:
            return eligible[0]
        return preview_ref or json_ref or fallback

    else:  # fidelity_first
        # Prefer full-inline unless it's egregiously large (>3x block tokens)
        FIDELITY_GUARD = 3.0
        eligible = [c for c in full_inline_sorted
                    if block_content_tokens <= 0 or c.token_count <= block_content_tokens * FIDELITY_GUARD]
        if eligible:
            return eligible[0]
        return preview_ref or json_ref or fallback


def render_table_for_payload(
    table_data,
    profile: PackageProfile | None = None,
    block_id: str | None = None,
    block_content: str | None = None,
    artifact_path: str | None = None,
    title: str | None = None,
) -> tuple[str, object]:
    """Canonical table serialization. Returns (text, candidate) for the selected format.

    Backward-compatible: callers that only need the text can use [0].
    """
    from .models import TablePayloadFormat, TableSerializationCandidate
    from .token_accounting import count_text_tokens

    if profile is None:
        from .models import PackageProfile
        profile = PackageProfile()

    # Legacy json_reference override (kept for backward compat)
    fmt = getattr(profile, "table_payload_format", "markdown")
    if fmt == "json_reference":
        tid = getattr(table_data, "id", "") or block_id or "unnamed"
        text = f"[Table: {tid}]"
        cand = TableSerializationCandidate(
            format=TablePayloadFormat.JSON_REFERENCE, text=text,
            token_count=count_text_tokens(text),
            preserves_all_rows_inline=False, preserves_structure_inline=False,
            artifact_path=artifact_path,
        )
        return text, cand

    table_id = getattr(table_data, "id", None) or block_id or "table"
    block_tokens = count_text_tokens(block_content or "")
    mode = getattr(profile, "mode", "adaptive")
    if hasattr(mode, "value"):
        mode = mode.value

    candidates = build_table_candidates(
        table_data, table_id, artifact_path, block_tokens, profile, title=title
    )
    selected = select_table_serialization(candidates, mode, profile, block_tokens)
    return selected.text, selected


def _item_id(document_id: str, element_id: str) -> str:
    raw = f"item:{document_id}:{element_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _is_heading_block(block: object) -> bool:
    try:
        from ..models.block import BlockType
        return block.type == BlockType.HEADING  # type: ignore[attr-defined]
    except Exception:
        return False


def _is_linked_table_fallback(elem: PackageElementPlan) -> bool:
    """True if this element is a TABLE_VISUAL_FALLBACK (stays paired with its source block)."""
    return str(elem.reason_code) == ReasonCode.TABLE_VISUAL_FALLBACK


def build_llm_payload(
    plan: DocumentPackagePlan,
    document: Document,
    package_dir: Path | str,
    asset_refs: list[PackageAssetReference],
    profile: PackageProfile | None = None,
) -> LLMPayload:
    """Build an LLMPayload from a finalized DocumentPackagePlan.

    Ordering:
    1. Block-backed elements and their linked table visual fallbacks stay in plan order.
    2. Page-level fallbacks (TABLE_EXPECTED_NOT_EXTRACTED) are interleaved after the last
       block element on that page, sorted by page ascending.
    """
    if profile is None:
        profile = PackageProfile()

    package_dir = Path(package_dir)
    document_id = plan.document_id

    # Build block lookup
    block_by_id: dict[str, object] = {b.id: b for b in document.blocks}

    # Build asset ref lookup: element_id -> first matching ref
    asset_ref_by_element: dict[str, PackageAssetReference] = {}
    for ref in asset_refs:
        for eid in ref.related_element_ids:
            if eid not in asset_ref_by_element:
                asset_ref_by_element[eid] = ref

    # ── Split elements into block/linked-fallback vs page fallbacks ────────────
    block_elements: list[PackageElementPlan] = []
    page_fallbacks: list[PackageElementPlan] = []

    for elem in plan.elements:
        if elem.source_kind == PackageSourceKind.BLOCK:
            block_elements.append(elem)
        elif elem.source_kind in (PackageSourceKind.PAGE_REGION, PackageSourceKind.PAGE):
            if _is_linked_table_fallback(elem):
                # Keep paired with block elements in plan order
                block_elements.append(elem)
            else:
                page_fallbacks.append(elem)

    # Sort page fallbacks by page ascending
    page_fallbacks.sort(key=lambda e: e.page or 0)

    # ── Interleave page fallbacks after last block on their page ───────────────
    # Build map: page -> index of last block element on that page
    page_to_last_block_idx: dict[int, int] = {}
    for i, elem in enumerate(block_elements):
        if elem.page is not None:
            page_to_last_block_idx[elem.page] = i

    # Insert page fallbacks in reverse so indices don't shift badly
    ordered: list[PackageElementPlan] = list(block_elements)
    for fb in reversed(page_fallbacks):
        fb_page = fb.page or 0
        # Find insertion position: after last block on fb_page or earlier page
        insert_after = -1
        best_page = -1
        for pg, idx in page_to_last_block_idx.items():
            if pg <= fb_page and pg > best_page:
                best_page = pg
                insert_after = idx
        if insert_after >= 0:
            ordered.insert(insert_after + 1, fb)
        else:
            ordered.append(fb)

    # ── Build caption-consumed map ─────────────────────────────────────────────
    # If a caption element has CAPTION_OF relationship pointing to an image element,
    # that caption should not appear as a separate TEXT item.
    caption_consumed: set[str] = set()  # element_ids of captions already linked to images
    # Map from image element_id -> caption element_id (for looking up caption text)
    image_to_caption_elem: dict[str, str] = {}

    for elem in plan.elements:
        for rel in elem.relationships:
            if rel.relationship_type == RelationshipType.CAPTION_OF:
                # elem is a caption; rel.target_element_id is the image element
                caption_consumed.add(elem.element_id)
                image_to_caption_elem[rel.target_element_id] = elem.element_id

    # Build element_id -> element map for caption text lookup
    elem_by_id: dict[str, PackageElementPlan] = {e.element_id: e for e in plan.elements}

    # ── Generate items ─────────────────────────────────────────────────────────
    items: list[LLMPayloadItem] = []
    all_unresolved: list[str] = []
    all_missing_paths: list[str] = []
    skipped_captions = 0
    selected_visual_count = 0
    representation_downgrades: list[str] = []

    # Token delta breakdown tracking
    _caption_dedup_total: int = 0
    _warning_total: int = 0

    # Track emitted element IDs for mismatch detection
    emitted_element_ids: set[str] = set()

    for elem in ordered:
        rep = elem.representation

        # Skip OMIT and REFERENCE_ONLY
        if not elem.include_by_default:
            continue
        if rep in (RepresentationType.OMIT, RepresentationType.REFERENCE_ONLY):
            continue

        # Skip caption elements that are consumed by images; track their token cost
        if elem.element_id in caption_consumed:
            skipped_captions += 1
            # Track how many tokens this caption would have contributed
            cap_block_id = elem.block_id
            cap_block = block_by_id.get(cap_block_id) if cap_block_id else None
            if cap_block:
                _caption_dedup_total += count_text_tokens(cap_block.content or "")
            continue

        eid = elem.element_id
        iid = _item_id(document_id, eid)
        block_id = elem.block_id
        block = block_by_id.get(block_id) if block_id else None

        provenance: dict = {}
        if profile.include_provenance:
            provenance = {
                "reason_code": str(elem.reason_code),
                "source_kind": elem.source_kind.value,
                "representation": rep.value,
            }

        # ── MARKDOWN (text, headings, formulas, legacy tables) ─────────────────
        if rep == RepresentationType.MARKDOWN:
            if block is not None and _is_heading_block(block):
                level = block.level or 1
                text_content = "#" * level + " " + (block.content or "")
            else:
                text_content = (block.content if block else None) or ""
            tok = count_text_tokens(text_content)
            item = LLMPayloadItem(
                item_id=iid,
                content_type=PayloadContentType.TEXT,
                document_id=document_id,
                element_id=eid,
                block_id=block_id,
                page=elem.page,
                text=text_content,
                estimated_tokens=tok,
                warning_codes=list(elem.warning_codes),
                provenance=provenance,
            )
            items.append(item)
            emitted_element_ids.add(eid)

        # ── STRUCTURED_TABLE ───────────────────────────────────────────────────
        elif rep == RepresentationType.STRUCTURED_TABLE:
            table_markdown: str | None = None
            table_artifact_path: str | None = None
            caption_text: str | None = None
            table_candidate = None

            if block is not None and block.table_data is not None:
                artifact_path = f"tables/{block.id}.json"
                if (package_dir / artifact_path).exists():
                    table_artifact_path = artifact_path

                table_text, table_candidate = render_table_for_payload(
                    block.table_data, profile,
                    block_id=block.id,
                    block_content=block.content,
                    artifact_path=table_artifact_path,
                )
                table_markdown = table_text

            # Check if there's a caption for this image element
            cap_elem_id = image_to_caption_elem.get(eid)
            if cap_elem_id:
                cap_elem = elem_by_id.get(cap_elem_id)
                if cap_elem and cap_elem.block_id:
                    cap_block = block_by_id.get(cap_elem.block_id)
                    if cap_block:
                        caption_text = cap_block.content or None

            tok = count_text_tokens(table_markdown or "")

            # Extract table serialization metadata from candidate
            tpf = None
            t_rows_total = 0
            t_rows_inline = 0
            t_rows_omitted = 0
            t_cols_total = 0
            t_inline_complete = True
            if table_candidate is not None:
                tpf = str(table_candidate.format)
                t_rows_omitted = table_candidate.omitted_row_count
                t_inline_complete = table_candidate.preserves_all_rows_inline
                if block is not None and block.table_data is not None:
                    td = block.table_data
                    header_count = len(td.header_rows) if td.header_rows else 0
                    body_rows = max(0, td.row_count - header_count)
                    t_rows_total = body_rows
                    t_rows_inline = max(0, body_rows - t_rows_omitted)
                    t_cols_total = td.column_count

            item = LLMPayloadItem(
                item_id=iid,
                content_type=PayloadContentType.STRUCTURED_TABLE,
                document_id=document_id,
                element_id=eid,
                block_id=block_id,
                page=elem.page,
                table_markdown=table_markdown,
                table_artifact_path=table_artifact_path,
                caption=caption_text,
                estimated_tokens=tok,
                warning_codes=list(elem.warning_codes),
                provenance=provenance,
                table_payload_format=tpf,
                table_rows_total=t_rows_total,
                table_rows_inline=t_rows_inline,
                table_rows_omitted=t_rows_omitted,
                table_columns_total=t_cols_total,
                full_table_artifact_path=table_artifact_path,
                inline_complete=t_inline_complete,
            )
            items.append(item)
            emitted_element_ids.add(eid)
            if table_artifact_path:
                selected_visual_count += 1

        # ── IMAGE (block source) ───────────────────────────────────────────────
        elif rep == RepresentationType.IMAGE and elem.source_kind == PackageSourceKind.BLOCK:
            asset_ref = asset_ref_by_element.get(eid)
            asset_path: str | None = None

            if asset_ref is not None:
                candidate = asset_ref.file_path
                if (package_dir / candidate).exists():
                    asset_path = candidate
                else:
                    all_unresolved.append(eid)
                    all_missing_paths.append(candidate)
            else:
                # No asset ref at all
                pass

            # Get caption from block metadata or adjacent caption element
            caption_text = None
            if block is not None:
                caption_text = block.metadata.get("caption") or block.metadata.get("alt_text")
            cap_elem_id = image_to_caption_elem.get(eid)
            if cap_elem_id and not caption_text:
                cap_elem = elem_by_id.get(cap_elem_id)
                if cap_elem and cap_elem.block_id:
                    cap_block = block_by_id.get(cap_elem.block_id)
                    if cap_block:
                        caption_text = cap_block.content or None

            tok = count_text_tokens(caption_text or "")
            item = LLMPayloadItem(
                item_id=iid,
                content_type=PayloadContentType.IMAGE_REFERENCE,
                document_id=document_id,
                element_id=eid,
                block_id=block_id,
                page=elem.page,
                asset_path=asset_path,
                caption=caption_text or None,
                estimated_tokens=tok,
                warning_codes=list(elem.warning_codes),
                provenance=provenance,
            )
            items.append(item)
            emitted_element_ids.add(eid)
            if asset_path:
                selected_visual_count += 1

        # ── IMAGE (page_region or page source — visual fallbacks / page fallbacks)
        elif rep == RepresentationType.IMAGE and elem.source_kind in (
            PackageSourceKind.PAGE_REGION, PackageSourceKind.PAGE
        ):
            asset_ref = asset_ref_by_element.get(eid)
            asset_path = None

            if asset_ref is not None:
                candidate = asset_ref.file_path
                if (package_dir / candidate).exists():
                    asset_path = candidate
                else:
                    all_missing_paths.append(candidate)

            # Emit WARNING item if no asset and this is a page fallback (not linked table fallback)
            if asset_path is None and not _is_linked_table_fallback(elem):
                if profile.include_warning_items and elem.element_type == "table":
                    warn_text = f"[WARNING] Expected table on page {elem.page} was not extracted."
                    warn_tok = count_text_tokens(warn_text)
                    warn_prov = {
                        "reason_code": str(elem.reason_code),
                        "source_kind": elem.source_kind.value,
                        "representation": rep.value,
                    } if profile.include_provenance else {}
                    warn_item = LLMPayloadItem(
                        item_id=iid,
                        content_type=PayloadContentType.WARNING,
                        document_id=document_id,
                        element_id=eid,
                        page=elem.page,
                        text=warn_text,
                        warning_codes=list(elem.warning_codes),
                        provenance=warn_prov,
                        estimated_tokens=warn_tok,
                    )
                    items.append(warn_item)
                    _warning_total += warn_tok
                    all_unresolved.append(eid)
                    emitted_element_ids.add(eid)
                else:
                    all_unresolved.append(eid)
                continue

            item = LLMPayloadItem(
                item_id=iid,
                content_type=PayloadContentType.IMAGE_REFERENCE,
                document_id=document_id,
                element_id=eid,
                page=elem.page,
                asset_path=asset_path,
                estimated_tokens=0,
                warning_codes=list(elem.warning_codes),
                provenance=provenance,
            )
            items.append(item)
            emitted_element_ids.add(eid)
            if asset_path:
                selected_visual_count += 1

        # ── KEY_VALUE_GROUP ────────────────────────────────────────────────────
        elif rep == RepresentationType.KEY_VALUE_GROUP:
            from ..renderers.key_value_markdown import render_key_value_group, render_key_value_tsv

            kv_group = block.key_value_group if block is not None else None
            kv_artifact_path: str | None = None
            kv_text = ""
            kv_record_count = 0
            kv_entry_count = 0

            if kv_group is not None:
                # Check if artifact exists
                artifact_path_str = f"key_values/{block.id}.json"
                if (package_dir / artifact_path_str).exists():
                    kv_artifact_path = artifact_path_str

                # Serialize: default to markdown_list; TSV for large groups
                kv_entry_count = len(kv_group.entries)

                # Count records (groups separated by repeated keys)
                seen_k: set[str] = set()
                rec_count = 1
                for e in kv_group.entries:
                    if e.key in seen_k:
                        rec_count += 1
                        seen_k = {e.key}
                    else:
                        seen_k.add(e.key)
                kv_record_count = rec_count

                md_text = render_key_value_group(kv_group)
                tsv_text = render_key_value_tsv(kv_group)

                # Token-aware format selection: always pick the smaller valid serialization
                md_tok = count_text_tokens(md_text)
                tsv_tok = count_text_tokens(tsv_text)

                if kv_record_count > 1:
                    # Repeated-record groups: prefer TSV only when record boundaries remain clear.
                    # TSV renderer uses [Record N] markers, so boundaries are preserved.
                    kv_text = tsv_text if tsv_tok < md_tok else md_text
                    kv_selected_format = "tsv" if tsv_tok < md_tok else "markdown"
                else:
                    # Flat groups: always pick smaller
                    kv_text = tsv_text if tsv_tok < md_tok else md_text
                    kv_selected_format = "tsv" if tsv_tok < md_tok else "markdown"
            else:
                md_tok = 0
                tsv_tok = 0
                kv_selected_format = ""

            tok = count_text_tokens(kv_text)
            item = LLMPayloadItem(
                item_id=iid,
                content_type=PayloadContentType.KEY_VALUE_GROUP,
                document_id=document_id,
                element_id=eid,
                block_id=block_id,
                page=elem.page,
                text=kv_text,
                kv_artifact_path=kv_artifact_path,
                kv_record_count=kv_record_count,
                kv_entry_count=kv_entry_count,
                estimated_tokens=tok,
                warning_codes=list(elem.warning_codes),
                provenance=provenance,
                kv_selected_format=kv_selected_format,
                kv_markdown_tokens=md_tok,
                kv_tsv_tokens=tsv_tok,
            )
            items.append(item)
            emitted_element_ids.add(eid)

        # ── IMAGE_AND_TEXT (OCR text blocks) ───────────────────────────────────
        elif rep == RepresentationType.IMAGE_AND_TEXT:
            text_content = (block.content if block else None) or ""
            asset_ref = asset_ref_by_element.get(eid)
            asset_path = None
            if asset_ref is not None:
                candidate = asset_ref.file_path
                if (package_dir / candidate).exists():
                    asset_path = candidate

            image_available = asset_path is not None
            downgraded = not image_available

            tok = count_text_tokens(text_content)
            img_prov = dict(provenance)
            img_prov["representation"] = "image_and_text"
            img_prov["image_available"] = image_available
            if downgraded:
                img_prov["downgraded"] = True
                representation_downgrades.append(eid)
                all_unresolved.append(eid)

            item = LLMPayloadItem(
                item_id=iid,
                content_type=PayloadContentType.TEXT,
                document_id=document_id,
                element_id=eid,
                block_id=block_id,
                page=elem.page,
                text=text_content,
                asset_path=asset_path,
                estimated_tokens=tok,
                warning_codes=list(elem.warning_codes),
                provenance=img_prov,
            )
            items.append(item)
            emitted_element_ids.add(eid)

    # ── Token accounting ───────────────────────────────────────────────────────
    actual_text_token_count = sum(item.estimated_tokens for item in items)
    planned_text_tokens = plan.estimated_tokens
    token_delta = actual_text_token_count - planned_text_tokens

    # ── Token delta breakdown ──────────────────────────────────────────────────
    explained_delta = (-_caption_dedup_total) + _warning_total
    token_delta_breakdown = TokenDeltaBreakdown(
        caption_dedup_delta=-_caption_dedup_total,
        warning_delta=_warning_total,
        representation_downgrade_delta=0,   # IMAGE_AND_TEXT has same text-token count
        missing_asset_delta=0,
        other_delta=token_delta - explained_delta,
    )

    # ── Fidelity ───────────────────────────────────────────────────────────────
    planned_count = len([
        e for e in plan.elements
        if e.include_by_default
        and e.representation not in (RepresentationType.OMIT, RepresentationType.REFERENCE_ONLY)
        and e.element_id not in caption_consumed
    ])

    # Find plan_payload_mismatches: selected elements with no emitted item
    mismatches: list[str] = []
    for e in plan.elements:
        if (
            e.include_by_default
            and e.representation not in (RepresentationType.OMIT, RepresentationType.REFERENCE_ONLY)
            and e.element_id not in caption_consumed
            and e.element_id not in emitted_element_ids
        ):
            mismatches.append(e.element_id)

    fidelity = PayloadFidelity(
        planned_elements=planned_count,
        emitted_items=len(items),
        skipped_duplicate_captions=skipped_captions,
        unresolved_element_ids=list(set(all_unresolved)),
        missing_asset_paths=list(set(all_missing_paths)),
        plan_payload_mismatches=mismatches,
        representation_downgrades=representation_downgrades,
    )

    return LLMPayload(
        document_id=document_id,
        package_mode=plan.mode,
        planner_version=plan.planner_version,
        items=items,
        planned_text_tokens=planned_text_tokens,
        actual_text_token_count=actual_text_token_count,
        token_delta=token_delta,
        selected_visual_asset_count=selected_visual_count,
        unresolved_element_ids=list(set(all_unresolved)),
        fidelity=fidelity,
        token_delta_breakdown=token_delta_breakdown,
    )
