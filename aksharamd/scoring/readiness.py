"""Extraction Confidence Score.

Measures how much of the source document's content we believe was successfully
extracted -not how "AI-friendly" the output is. A clean text file should score
90+. A partially-scanned PDF should score 50-65.

Returns a ReadinessResult with a 0-100 integer score, structured deduction
records (including suppressed ones), and human-readable notes.
"""
from __future__ import annotations

from ..context import CompilationContext
from ..models.block import BlockType
from .models import (
    SCORING_POLICY_VERSION,
    DeductionRecord,
    ReadinessEvidence,
    ReadinessResult,
)

# Backward-compat re-export
ConfidenceResult = ReadinessResult

# ── Format-quality baselines ───────────────────────────────────────────────────
# Starting confidence before any signal-based adjustments. Reflects how lossy
# the conversion from the source format inherently is.
_FORMAT_BASE: dict[str, int] = {
    "md": 95, "markdown": 95,
    "txt": 93, "text": 93,
    "rst": 92, "tex": 90, "latex": 90,
    # source code
    "py": 95, "js": 95, "ts": 95, "jsx": 95, "tsx": 95, "go": 95,
    "rs": 95, "java": 95, "c": 95, "cpp": 95, "cs": 95, "rb": 95,
    "sh": 95, "sql": 95, "yaml": 95, "yml": 95, "toml": 95,
    # structured data
    "json": 88, "jsonl": 88, "ndjson": 88, "xml": 82, "csv": 90, "tsv": 90,
    "rss": 85, "atom": 85,
    # documents
    "pdf": 87,          # text-native; adjusted down if scanned pages detected
    "docx": 83, "docm": 80,
    "pptx": 80, "pptm": 78,
    "xlsx": 85, "xlsm": 83, "xls": 80,
    "odt": 80, "ods": 83, "odp": 78,
    "epub": 80,
    "rtf": 63,          # striprtf is lossy; images/tables lost
    "html": 87, "htm": 87,
    # email
    "eml": 78, "msg": 78,
    # notebooks
    "ipynb": 83,
    # archives
    "zip": 68, "tar": 68, "tgz": 68, "gz": 65, "bz2": 65, "xz": 65, "7z": 68,
    # media
    "jpg": 70, "jpeg": 70, "png": 70, "gif": 65, "bmp": 68,
    "tiff": 70, "tif": 70, "webp": 70,
    "mp3": 72, "wav": 75, "m4a": 72, "ogg": 70, "flac": 75,
    "mp4": 68, "webm": 65, "opus": 70, "aac": 70,
    # legacy (requires LibreOffice)
    "doc": 65, "ppt": 62,
}
_DEFAULT_BASE = 72


def compute_confidence(ctx: CompilationContext) -> ReadinessResult:
    """Compute extraction confidence score and structured deduction records."""
    if ctx.document is None:
        return ReadinessResult(
            score=0,
            notes=["No content could be extracted from this file."],
            scoring_policy_version=SCORING_POLICY_VERSION,
        )

    if ctx.original_tokens == 0:
        return ReadinessResult(
            score=10,
            notes=["Document appears to be empty or could not be parsed into tokens."],
            scoring_policy_version=SCORING_POLICY_VERSION,
        )

    doc = ctx.document
    blocks = doc.blocks
    file_type = doc.file_type or ""
    notes: list[str] = []
    deductions: list[DeductionRecord] = []
    informational: list[DeductionRecord] = []

    # ── Base score from format ─────────────────────────────────────────────────
    score = _FORMAT_BASE.get(file_type, _DEFAULT_BASE)

    # ── Block inventory ────────────────────────────────────────────────────────
    headings   = [b for b in blocks if b.type == BlockType.HEADING]
    tables     = [b for b in blocks if b.type == BlockType.TABLE]
    images     = [b for b in blocks if b.type == BlockType.IMAGE]
    paragraphs = [b for b in blocks if b.type == BlockType.PARAGRAPH]
    code_blocks = [b for b in blocks if b.type == BlockType.CODE_BLOCK]

    # ── Signals from validation issues ────────────────────────────────────────
    errors      = [i for i in ctx.validation.issues if i.severity.value == "error"]
    warnings_by_code: dict[str, int] = {}
    for i in ctx.validation.warnings:
        warnings_by_code[i.code] = warnings_by_code.get(i.code, 0) + 1

    # Parse errors
    if errors:
        deduction = min(30, len(errors) * 12)
        score -= deduction
        notes.append(f"{len(errors)} parse error(s) occurred -some content may be missing.")
        deductions.append(DeductionRecord(
            rule_id="PARSE_ERRORS",
            description=f"{len(errors)} parse error(s)",
            penalty=deduction,
            evidence=ReadinessEvidence(
                metric_name="error_count",
                metric_value=float(len(errors)),
                threshold=1.0,
            ),
        ))

    # Missing pages (PDF/DOCX)
    missing_pages = warnings_by_code.get("MISSING_PAGE", 0)
    if missing_pages > 0 and doc.pages > 0:
        pct = round(missing_pages / doc.pages * 100)
        base_ded = min(30, missing_pages * 4)
        extra_ded = 8 if pct >= 50 else 0
        total_ded = base_ded + extra_ded
        score -= total_ded
        if pct >= 50:
            notes.append(
                f"{missing_pages} of {doc.pages} pages have no extractable text "
                f"({pct}%) -document may be scanned or image-based. "
                "OCR was applied where possible; verify output accuracy."
            )
        else:
            notes.append(
                f"{missing_pages} of {doc.pages} pages appear image-only -"
                "OCR was applied, content on those pages may be partial."
            )
        deductions.append(DeductionRecord(
            rule_id="MISSING_PAGE",
            description=f"{missing_pages} of {doc.pages} pages missing ({pct}%)",
            penalty=total_ded,
            evidence=ReadinessEvidence(
                metric_name="missing_page_count",
                metric_value=float(missing_pages),
                threshold=1.0,
                extras={"total_pages": doc.pages, "missing_pct": pct},
            ),
        ))

    # Large blocks (likely a parse/merge failure)
    large_blocks = warnings_by_code.get("LARGE_BLOCK", 0)
    if large_blocks > 0:
        ded = min(10, large_blocks * 4)
        score -= ded
        notes.append(
            f"{large_blocks} unusually large block(s) detected -"
            "text may have been merged incorrectly in complex layout sections."
        )
        deductions.append(DeductionRecord(
            rule_id="LARGE_BLOCK",
            description=f"{large_blocks} large block(s) (>10 000 chars)",
            penalty=ded,
            evidence=ReadinessEvidence(
                metric_name="large_block_count",
                metric_value=float(large_blocks),
                threshold=1.0,
            ),
        ))

    # Heading hierarchy issues
    heading_issues = (
        warnings_by_code.get("HEADING_SKIP", 0)
        + warnings_by_code.get("HEADING_HIERARCHY", 0)
    )
    if heading_issues > 0:
        ded = min(8, heading_issues * 2)
        score -= ded
        deductions.append(DeductionRecord(
            rule_id="HEADING_ISSUES",
            description=f"{heading_issues} heading hierarchy issue(s)",
            penalty=ded,
            evidence=ReadinessEvidence(
                metric_name="heading_issue_count",
                metric_value=float(heading_issues),
                threshold=1.0,
            ),
        ))

    # ── New quality-signal penalties ───────────────────────────────────────────

    _IMAGE_PLACEHOLDER_SENTINEL = "[Image not extracted"
    placeholder_paragraphs = [
        b for b in blocks
        if b.type == BlockType.PARAGRAPH and _IMAGE_PLACEHOLDER_SENTINEL in (b.content or "")
    ]
    real_content_blocks = [
        b for b in blocks
        if b.type not in (BlockType.IMAGE,)
        and not (b.type == BlockType.PARAGRAPH and _IMAGE_PLACEHOLDER_SENTINEL in (b.content or ""))
    ]
    image_placeholder_only = (
        bool(placeholder_paragraphs)
        and not real_content_blocks
        and not warnings_by_code.get("OCR_REQUIRED", 0)
    )
    if image_placeholder_only:
        assets_with_bytes = [a for a in doc.assets if a.image_bytes]
        if assets_with_bytes:
            notes.append(
                "Text extraction found no content — page is a raster image. "
                "Full-page image data is captured and will be included for vision-capable "
                "models via compile_to_multimodal(). "
                "Text-only usage requires OCR: pip install aksharamd[ocr]. "
                "[W_IMAGE_ONLY_REQUIRES_VISION]"
            )
            informational.append(DeductionRecord(
                rule_id="IMAGE_PLACEHOLDER_WITH_ASSETS",
                description="Image-only page; asset bytes available for multimodal use",
                penalty=0,
            ))
        else:
            effective_penalty = max(0, score - 55)  # compute before capping
            score = min(score, 55)
            notes.append(
                "Output contains only image placeholders — no text was extracted and "
                "no image assets are available. "
                "OCR support is required: pip install aksharamd[ocr]. "
                "[W_IMAGE_ONLY_NO_USABLE_FALLBACK]"
            )
            deductions.append(DeductionRecord(
                rule_id="IMAGE_PLACEHOLDER_NO_FALLBACK",
                description="Output is image placeholders only; score capped at 55",
                penalty=effective_penalty,
                evidence=ReadinessEvidence(
                    metric_name="placeholder_count",
                    metric_value=float(len(placeholder_paragraphs)),
                    threshold=1.0,
                ),
            ))

    # OCR required but unavailable
    ocr_required_fired = bool(warnings_by_code.get("OCR_REQUIRED", 0))
    if ocr_required_fired:
        classification = doc.metadata.get("pdf_classification", "")
        image_pages = doc.metadata.get("pdf_stats", {}).get("image_pages", 0)
        total_pages = max(doc.pages, 1)
        image_ratio = image_pages / total_pages
        ded = min(40, int(40 * image_ratio) + 10)
        score -= ded
        notes.append(
            f"PDF is '{classification}' with {image_pages} image-only page(s) — "
            "OCR not installed; this content was not extracted. "
            "Install pytesseract for full extraction: pip install aksharamd[ocr]"
        )
        deductions.append(DeductionRecord(
            rule_id="OCR_REQUIRED",
            description=f"OCR unavailable; {image_pages} image page(s) not extracted",
            penalty=ded,
            evidence=ReadinessEvidence(
                metric_name="image_ratio",
                metric_value=image_ratio,
                threshold=0.0,
                extras={"image_pages": image_pages, "total_pages": total_pages, "classification": classification},
            ),
        ))

    # OCR attempted but produced sparse output
    ocr_attempted_sparse = (
        not ocr_required_fired
        and file_type == "pdf"
        and bool(warnings_by_code.get("NEAR_EMPTY_OUTPUT", 0))
        and doc.metadata.get("pdf_ocr_available", False)
    )
    if ocr_attempted_sparse:
        image_pages = doc.metadata.get("pdf_stats", {}).get("image_pages", 0)
        total_pages = max(doc.pages, 1)
        image_ratio = image_pages / total_pages
        ded = min(40, int(40 * image_ratio) + 10)
        score -= ded
        notes.append(
            f"OCR was applied to {image_pages} image page(s) but extracted very little text — "
            "the page(s) may contain rotated content, low-resolution scans, or non-Latin script. "
            "For better results, try a higher DPI (AKSHARAMD_OCR_DPI=300) or a vision-based tool."
        )
        deductions.append(DeductionRecord(
            rule_id="OCR_ATTEMPTED_SPARSE",
            description=f"OCR ran but produced sparse output on {image_pages} image page(s)",
            penalty=ded,
            evidence=ReadinessEvidence(
                metric_name="image_ratio",
                metric_value=image_ratio,
                threshold=0.0,
                extras={"image_pages": image_pages, "total_pages": total_pages},
            ),
        ))

    # Near-empty output
    if warnings_by_code.get("NEAR_EMPTY_OUTPUT", 0):
        if ocr_required_fired or ocr_attempted_sparse:
            deductions.append(DeductionRecord(
                rule_id="NEAR_EMPTY_OUTPUT",
                description="Very little text extracted",
                penalty=25,
                suppressed=True,
                suppression_reason="OCR_REQUIRED or OCR_ATTEMPTED_SPARSE already deducts for missing content",
            ))
        else:
            score -= 25
            notes.append(
                "Output is nearly empty relative to page count — "
                "source document may be image-only, encrypted, or have encoding issues."
            )
            deductions.append(DeductionRecord(
                rule_id="NEAR_EMPTY_OUTPUT",
                description="Very little text extracted relative to page count",
                penalty=25,
            ))

    # Low text density
    if warnings_by_code.get("LOW_TEXT_DENSITY", 0):
        if ocr_required_fired or ocr_attempted_sparse:
            deductions.append(DeductionRecord(
                rule_id="LOW_TEXT_DENSITY",
                description="Low text density (PDF)",
                penalty=20,
                suppressed=True,
                suppression_reason="OCR_REQUIRED or OCR_ATTEMPTED_SPARSE already deducts for missing content",
            ))
        else:
            score -= 20
            notes.append(
                "Low text density detected — extracted text is sparse relative to page count. "
                "Enable OCR for image-heavy pages: pip install aksharamd[ocr]"
            )
            deductions.append(DeductionRecord(
                rule_id="LOW_TEXT_DENSITY",
                description="Low text density",
                penalty=20,
            ))

    # CID glyph artifacts
    if warnings_by_code.get("GLYPH_ARTIFACTS", 0):
        score -= 25
        notes.append(
            "CID font artifacts detected in extracted text — "
            "PDF uses non-embedded fonts; portions of the text may be unreadable."
        )
        deductions.append(DeductionRecord(
            rule_id="GLYPH_ARTIFACTS",
            description="CID glyph artifacts — non-embedded fonts; text likely garbled",
            penalty=25,
        ))

    # Repeated content
    if warnings_by_code.get("REPEATED_CONTENT", 0):
        score -= 8
        notes.append(
            "Repeated content lines detected — "
            "headers, footers, or boilerplate may not have been fully removed."
        )
        deductions.append(DeductionRecord(
            rule_id="REPEATED_CONTENT",
            description="Repeated content lines — boilerplate not fully removed",
            penalty=8,
        ))

    # Token bloat
    if warnings_by_code.get("TOKEN_BLOAT", 0):
        score -= 8
        notes.append(
            "Unusually high token count per page — "
            "content may have been extracted multiple times or boilerplate was not removed."
        )
        deductions.append(DeductionRecord(
            rule_id="TOKEN_BLOAT",
            description="Unusually high token count per page",
            penalty=8,
        ))

    # ── Structural signals ─────────────────────────────────────────────────────

    if not headings and doc.pages > 3:
        score -= 6
        notes.append(
            "No headings detected -document structure may be flat or "
            "heading formatting was not preserved."
        )
        deductions.append(DeductionRecord(
            rule_id="NO_HEADINGS_MULTIPAGE",
            description=f"No headings in a {doc.pages}-page document",
            penalty=6,
            evidence=ReadinessEvidence(
                metric_name="heading_count",
                metric_value=0.0,
                threshold=1.0,
                extras={"pages": doc.pages},
            ),
        ))

    # ── Positive observations (notes only, no score change) ───────────────────

    parts = []
    if paragraphs:
        parts.append(f"{len(paragraphs)} paragraph(s)")
    if headings:
        levels = sorted({h.level for h in headings if h.level})
        level_str = ", ".join(f"H{lvl}" for lvl in levels)
        parts.append(f"{len(headings)} heading(s) ({level_str})")
    if tables:
        parts.append(f"{len(tables)} table(s)")
    if code_blocks:
        parts.append(f"{len(code_blocks)} code block(s)")
    if images:
        parts.append(f"{len(images)} image reference(s)")

    if parts:
        notes.insert(0, "Extracted: " + ", ".join(parts) + ".")

    # Table quality note
    if tables:
        col_generic = sum(
            1 for b in tables
            if "Col1" in b.content or "Col2" in b.content
        )
        if col_generic:
            ded = min(5, col_generic * 2)
            score -= ded
            notes.append(
                f"{col_generic} table(s) have auto-generated column headers -"
                "these may be visual/scanned tables. Verify column names."
            )
            deductions.append(DeductionRecord(
                rule_id="COL_GENERIC_TABLES",
                description=f"{col_generic} table(s) with auto-generated column headers",
                penalty=ded,
                evidence=ReadinessEvidence(
                    metric_name="col_generic_count",
                    metric_value=float(col_generic),
                    threshold=1.0,
                ),
            ))
        else:
            notes.append(f"{len(tables)} table(s) extracted with named columns.")

    # Images with no OCR text
    if images:
        notes.append(
            f"{len(images)} image(s) found -"
            + ("text content extracted via OCR." if file_type in ("jpg", "jpeg", "png", "tiff", "tif", "bmp", "webp", "gif")
               else "image content not transcribed.")
        )

    # PDF classification note
    if file_type == "pdf":
        classification = doc.metadata.get("pdf_classification", "")
        stats = doc.metadata.get("pdf_stats", {})
        if classification:
            label_map = {
                "native_text": "native text PDF",
                "scanned": "scanned/image PDF",
                "hybrid": "hybrid PDF (mixed text and image pages)",
                "table_heavy": "table-heavy PDF",
                "layout_heavy": "multi-column/layout-heavy PDF",
                "low_confidence": "PDF with low extraction confidence",
            }
            label = label_map.get(classification, classification)
            ip = stats.get("image_pages", 0)
            tp = stats.get("table_pages", 0)
            detail_parts = []
            if ip:
                detail_parts.append(f"{ip} image-only page(s)")
            if tp:
                detail_parts.append(f"{tp} table page(s)")
            detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
            notes.append(f"PDF classified as: {label}{detail}.")

    # Format-specific notes
    if file_type == "rtf":
        notes.append("RTF conversion is lossy -images, complex tables, and embedded objects are not preserved.")
    elif file_type in ("doc", "ppt"):
        notes.append("Legacy Office format -converted via LibreOffice. Some formatting may differ from the original.")
    elif file_type in ("mp3", "wav", "m4a", "ogg", "flac", "webm", "opus", "aac"):
        notes.append(
            "Audio transcribed using Whisper (base model). "
            "Accuracy is typically 65-80% for clear speech. "
            "Technical terms, names, and accents may be inaccurate."
        )
    elif file_type == "mp4":
        notes.append(
            "Video: audio track transcribed via Whisper. Visual content (slides, charts, text on screen) not extracted."
        )
    elif file_type in ("zip", "tar", "tgz", "gz", "bz2", "xz", "7z"):
        notes.append("Archive: file listing and readable text files extracted. Binary files are not included.")
    elif file_type in ("jpg", "jpeg", "png", "gif", "tiff", "tif", "bmp", "webp"):
        if not paragraphs:
            score -= 10
            notes.append("No text detected in image -file may be a photo or diagram with no readable text.")
            deductions.append(DeductionRecord(
                rule_id="NO_TEXT_IN_IMAGE",
                description="Image file has no extractable text",
                penalty=10,
                evidence=ReadinessEvidence(metric_name="paragraph_count", metric_value=0.0, threshold=1.0),
            ))
        else:
            notes.append("Text extracted via OCR (Tesseract). Accuracy depends on image quality and font clarity.")

    # W_MULTICOLUMN_ORDER — score cap at 69 (RISKY band).
    # Rationale: candidate-maturity signal, precision 100% on calibrated fixtures.
    # Reading-order failure means content is present but interleaved — RISKY per
    # docs/readiness-score.md ("extraction is partial or degraded").
    # See docs/calibration/SCORING_POLICY.md for the cap decision.
    _MC_CAP = 69
    if warnings_by_code.get("W_MULTICOLUMN_ORDER", 0):
        mc_diag = doc.metadata.get("multicolumn_diagnostics", {})
        mc_maturity = mc_diag.get("warning_maturity", "")
        problem_pages = mc_diag.get("problem_pages", []) or []
        if score > _MC_CAP:
            effective_penalty = score - _MC_CAP
            score = _MC_CAP
            notes.append(
                "Multi-column reading order likely incorrect — content from adjacent columns "
                f"may be interleaved. Score capped at {_MC_CAP} (RISKY band). "
                "[W_MULTICOLUMN_ORDER]"
            )
            deductions.append(DeductionRecord(
                rule_id="W_MULTICOLUMN_ORDER",
                description=f"Multi-column reading order failure; score capped at {_MC_CAP}",
                penalty=effective_penalty,
                maturity=mc_maturity,
                evidence=ReadinessEvidence(
                    metric_name="problem_page_count",
                    metric_value=float(len(problem_pages)),
                    threshold=1.0,
                ),
            ))
        else:
            deductions.append(DeductionRecord(
                rule_id="W_MULTICOLUMN_ORDER",
                description=f"Multi-column reading order failure; cap ({_MC_CAP}) did not apply",
                penalty=0,
                suppressed=True,
                suppression_reason=f"score already <= {_MC_CAP}",
                maturity=mc_maturity,
                evidence=ReadinessEvidence(
                    metric_name="problem_page_count",
                    metric_value=float(len(problem_pages)),
                    threshold=1.0,
                ),
            ))

    # W_HEADER_FOOTER_TABLE_GARBLED — score cap at 84 (top of OK band).
    # Rationale: experimental-maturity signal (calibrated on n=1 known positive).
    # Softer cap than W_MULTICOLUMN_ORDER while the evidence base grows.
    # The USP claim ("no known-issue warning at HIGH") is preserved by dropping
    # the doc out of HIGH; the softer cap limits damage from a hypothetical FP.
    # See docs/calibration/SCORING_POLICY.md for the cap decision.
    _HFT_CAP = 84
    if warnings_by_code.get("W_HEADER_FOOTER_TABLE_GARBLED", 0):
        hft_diag = doc.metadata.get("header_footer_table_diagnostics", {})
        hft_maturity = hft_diag.get("warning_maturity", "")
        problem_tables = hft_diag.get("problem_tables", []) or []
        if score > _HFT_CAP:
            effective_penalty = score - _HFT_CAP
            score = _HFT_CAP
            notes.append(
                "A table detected near a page header or footer appears fragmented — "
                "the output may include page furniture (running headers, page numbers, "
                f"column titles) rather than a meaningful table. Score capped at {_HFT_CAP} "
                "(top of OK band). [W_HEADER_FOOTER_TABLE_GARBLED]"
            )
            deductions.append(DeductionRecord(
                rule_id="W_HEADER_FOOTER_TABLE_GARBLED",
                description=f"Header/footer table garbling; score capped at {_HFT_CAP}",
                penalty=effective_penalty,
                maturity=hft_maturity,
                evidence=ReadinessEvidence(
                    metric_name="problem_table_count",
                    metric_value=float(len(problem_tables)),
                    threshold=1.0,
                ),
            ))
        else:
            deductions.append(DeductionRecord(
                rule_id="W_HEADER_FOOTER_TABLE_GARBLED",
                description=f"Header/footer table garbling; cap ({_HFT_CAP}) did not apply",
                penalty=0,
                suppressed=True,
                suppression_reason=f"score already <= {_HFT_CAP}",
                maturity=hft_maturity,
                evidence=ReadinessEvidence(
                    metric_name="problem_table_count",
                    metric_value=float(len(problem_tables)),
                    threshold=1.0,
                ),
            ))

    # W_TABLE_MISSING — score cap at 69 (RISKY band).
    # Rationale: candidate-maturity signal (Phase 3 detection PR #111).
    # Leader-dot density indicates a TOC or table was flattened to prose;
    # content is present but structural claim is missing. Same treatment as
    # W_MULTICOLUMN_ORDER (candidate + partial/degraded content).
    # See docs/calibration/SCORING_POLICY.md for the cap decision.
    _TM_CAP = 69
    if warnings_by_code.get("W_TABLE_MISSING", 0):
        tm_diag = doc.metadata.get("table_missing_diagnostics", {})
        tm_maturity = tm_diag.get("warning_maturity", "")
        leader_dot_lines = tm_diag.get("leader_dot_lines", 0)
        total_matches = tm_diag.get("total_leader_dot_matches", 0)
        if score > _TM_CAP:
            effective_penalty = score - _TM_CAP
            score = _TM_CAP
            notes.append(
                "Leader-dot density suggests a table of contents or table was flattened "
                f"to prose without its structure. Score capped at {_TM_CAP} (RISKY band). "
                "[W_TABLE_MISSING]"
            )
            deductions.append(DeductionRecord(
                rule_id="W_TABLE_MISSING",
                description=f"Table/TOC flattened to prose; score capped at {_TM_CAP}",
                penalty=effective_penalty,
                maturity=tm_maturity,
                evidence=ReadinessEvidence(
                    metric_name="leader_dot_lines",
                    metric_value=float(leader_dot_lines),
                    threshold=3.0,
                    extras={"total_leader_dot_matches": int(total_matches)},
                ),
            ))
        else:
            deductions.append(DeductionRecord(
                rule_id="W_TABLE_MISSING",
                description=f"Table/TOC flattened to prose; cap ({_TM_CAP}) did not apply",
                penalty=0,
                suppressed=True,
                suppression_reason=f"score already <= {_TM_CAP}",
                maturity=tm_maturity,
                evidence=ReadinessEvidence(
                    metric_name="leader_dot_lines",
                    metric_value=float(leader_dot_lines),
                    threshold=3.0,
                    extras={"total_leader_dot_matches": int(total_matches)},
                ),
            ))

    # W_ENCODING_ARTIFACTS — score cap at 69 (RISKY band).
    # Rationale: candidate-maturity signal (Phase 3 detection PR #111).
    # XML tag residue or mojibake density directly indicates the encoding /
    # segmentation stage of the parser failed on part of the content. Same
    # treatment as W_MULTICOLUMN_ORDER and W_TABLE_MISSING.
    # See docs/calibration/SCORING_POLICY.md for the cap decision.
    _EA_CAP = 69
    if warnings_by_code.get("W_ENCODING_ARTIFACTS", 0):
        ea_diag = doc.metadata.get("encoding_artifacts_diagnostics", {})
        ea_maturity = ea_diag.get("warning_maturity", "")
        xml_fragment_count = ea_diag.get("xml_fragment_count", 0)
        mojibake_density = ea_diag.get("mojibake_density", 0.0)
        if score > _EA_CAP:
            effective_penalty = score - _EA_CAP
            score = _EA_CAP
            notes.append(
                "Encoding or segmentation artifacts detected in extracted output — "
                f"content may be partially corrupt or missing. Score capped at {_EA_CAP} "
                "(RISKY band). [W_ENCODING_ARTIFACTS]"
            )
            deductions.append(DeductionRecord(
                rule_id="W_ENCODING_ARTIFACTS",
                description=f"Encoding/segmentation artifacts; score capped at {_EA_CAP}",
                penalty=effective_penalty,
                maturity=ea_maturity,
                evidence=ReadinessEvidence(
                    metric_name="xml_fragment_count",
                    metric_value=float(xml_fragment_count),
                    threshold=3.0,
                    extras={"mojibake_density": float(mojibake_density)},
                ),
            ))
        else:
            deductions.append(DeductionRecord(
                rule_id="W_ENCODING_ARTIFACTS",
                description=f"Encoding/segmentation artifacts; cap ({_EA_CAP}) did not apply",
                penalty=0,
                suppressed=True,
                suppression_reason=f"score already <= {_EA_CAP}",
                maturity=ea_maturity,
                evidence=ReadinessEvidence(
                    metric_name="xml_fragment_count",
                    metric_value=float(xml_fragment_count),
                    threshold=3.0,
                    extras={"mojibake_density": float(mojibake_density)},
                ),
            ))

    # Informational: W_PDF_ATTACHMENT_IGNORED (zero penalty)
    # Attachment payloads are separate from page content; AksharaMD detects but
    # does not extract them. Surfaced as informational so a HIGH readiness on
    # an attachment-bearing PDF is no longer silent.
    if warnings_by_code.get("W_PDF_ATTACHMENT_IGNORED", 0):
        att_diag = doc.metadata.get("pdf_attachment_diagnostics", {})
        att_maturity = att_diag.get("warning_maturity", "")
        att_count = att_diag.get("attachment_count", 0)
        informational.append(DeductionRecord(
            rule_id="W_PDF_ATTACHMENT_IGNORED",
            description=f"PDF contains {att_count} embedded file attachment(s) that were not extracted",
            penalty=0,
            maturity=att_maturity,
        ))

    # Informational: AUTO_OCR_BACKEND_SELECTED / AUTO_OCR_BACKEND_FALLBACK
    # (PR 100, zero penalty). Both surface exactly what the Auto Policy v1
    # decided and, when applicable, WHY it fell back. Readiness itself is
    # unaffected — the whole point of ``auto`` is to bridge two working
    # backends transparently.
    if warnings_by_code.get("AUTO_OCR_BACKEND_SELECTED", 0):
        informational.append(DeductionRecord(
            rule_id="AUTO_OCR_BACKEND_SELECTED",
            description="Auto Policy v1 selected an OCR backend for this document",
            penalty=0,
            maturity="informational",
        ))
    if warnings_by_code.get("AUTO_OCR_BACKEND_FALLBACK", 0):
        informational.append(DeductionRecord(
            rule_id="AUTO_OCR_BACKEND_FALLBACK",
            description="Auto Policy v1 fell back from the preferred backend to Tesseract",
            penalty=0,
            maturity="informational",
        ))

    # Token efficiency note
    if ctx.original_tokens > 0 and ctx.manifest:
        saved = ctx.original_tokens - ctx.manifest.optimized_tokens
        if saved > 0:
            pct = round(saved / ctx.original_tokens * 100)
            notes.append(f"Optimiser removed {saved:,} redundant tokens ({pct}%) - headers, footers, duplicates.")

    return ReadinessResult(
        score=max(0, min(100, score)),
        notes=notes,
        deductions=deductions,
        informational=informational,
        scoring_policy_version=SCORING_POLICY_VERSION,
    )


def compute_readiness_score(ctx: CompilationContext) -> int:
    """Backwards-compatible entry point. Returns integer score only."""
    return compute_confidence(ctx).score
