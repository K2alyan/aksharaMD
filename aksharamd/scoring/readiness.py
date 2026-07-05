"""Extraction Confidence Score.

Measures how much of the source document's content we believe was successfully
extracted -not how "AI-friendly" the output is. A clean text file should score
90+. A partially-scanned PDF should score 50-65.

Returns a ConfidenceResult with a 0-100 integer score and a list of plain-English
notes the user can act on or display.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..context import CompilationContext
from ..models.block import BlockType

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


@dataclass
class ConfidenceResult:
    score: int
    notes: list[str] = field(default_factory=list)


def compute_confidence(ctx: CompilationContext) -> ConfidenceResult:
    """Compute extraction confidence score and human-readable notes."""
    if ctx.document is None:
        return ConfidenceResult(score=0, notes=["No content could be extracted from this file."])

    if ctx.original_tokens == 0:
        return ConfidenceResult(score=10, notes=["Document appears to be empty or could not be parsed into tokens."])

    doc = ctx.document
    blocks = doc.blocks
    file_type = doc.file_type or ""
    notes: list[str] = []

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

    # Missing pages (PDF/DOCX)
    missing_pages = warnings_by_code.get("MISSING_PAGE", 0)
    if missing_pages > 0 and doc.pages > 0:
        pct = round(missing_pages / doc.pages * 100)
        deduction = min(30, missing_pages * 4)
        score -= deduction
        if pct >= 50:
            notes.append(
                f"{missing_pages} of {doc.pages} pages have no extractable text "
                f"({pct}%) -document may be scanned or image-based. "
                "OCR was applied where possible; verify output accuracy."
            )
            # Scanned doc: lower base further
            score -= 8
        else:
            notes.append(
                f"{missing_pages} of {doc.pages} pages appear image-only -"
                "OCR was applied, content on those pages may be partial."
            )

    # Large blocks (likely a parse/merge failure)
    large_blocks = warnings_by_code.get("LARGE_BLOCK", 0)
    if large_blocks > 0:
        score -= min(10, large_blocks * 4)
        notes.append(
            f"{large_blocks} unusually large block(s) detected -"
            "text may have been merged incorrectly in complex layout sections."
        )

    # Heading hierarchy issues
    heading_issues = (
        warnings_by_code.get("HEADING_SKIP", 0)
        + warnings_by_code.get("HEADING_HIERARCHY", 0)
    )
    if heading_issues > 0:
        score -= min(8, heading_issues * 2)

    # ── New quality-signal penalties ───────────────────────────────────────────

    # OCR required but unavailable: most severe — content is simply missing
    if warnings_by_code.get("OCR_REQUIRED", 0):
        classification = doc.metadata.get("pdf_classification", "")
        image_pages = doc.metadata.get("pdf_stats", {}).get("image_pages", 0)
        total_pages = max(doc.pages, 1)
        image_ratio = image_pages / total_pages
        # Scale penalty: fully scanned = -40, hybrid = proportional
        deduction = min(40, int(40 * image_ratio) + 10)
        score -= deduction
        notes.append(
            f"PDF is '{classification}' with {image_pages} image-only page(s) — "
            "OCR not installed; this content was not extracted. "
            "Install pytesseract for full extraction: pip install aksharamd[ocr]"
        )

    # Near-empty output: catastrophic — essentially nothing was extracted
    if warnings_by_code.get("NEAR_EMPTY_OUTPUT", 0):
        score -= 25
        notes.append(
            "Output is nearly empty relative to page count — "
            "source document may be image-only, encrypted, or have encoding issues."
        )

    # Low text density: serious quality signal
    if warnings_by_code.get("LOW_TEXT_DENSITY", 0):
        score -= 20
        notes.append(
            "Low text density detected — extracted text is sparse relative to page count. "
            "Enable OCR for image-heavy pages: pip install aksharamd[ocr]"
        )

    # CID glyph artifacts: extracted text is likely garbled
    if warnings_by_code.get("GLYPH_ARTIFACTS", 0):
        score -= 15
        notes.append(
            "CID font artifacts detected in extracted text — "
            "PDF uses non-embedded fonts; portions of the text may be unreadable."
        )

    # Repeated content: boilerplate not cleaned
    if warnings_by_code.get("REPEATED_CONTENT", 0):
        score -= 8
        notes.append(
            "Repeated content lines detected — "
            "headers, footers, or boilerplate may not have been fully removed."
        )

    # Token bloat: likely duplication or failed cleanup
    if warnings_by_code.get("TOKEN_BLOAT", 0):
        score -= 8
        notes.append(
            "Unusually high token count per page — "
            "content may have been extracted multiple times or boilerplate was not removed."
        )

    # ── Structural signals ─────────────────────────────────────────────────────

    # No headings in a multi-page document → structure likely lost
    if not headings and doc.pages > 3:
        score -= 6
        notes.append(
            "No headings detected -document structure may be flat or "
            "heading formatting was not preserved."
        )

    # ── Positive observations (notes only, no score change) ───────────────────

    # Summary line -always first
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
            score -= min(5, col_generic * 2)
            notes.append(
                f"{col_generic} table(s) have auto-generated column headers -"
                "these may be visual/scanned tables. Verify column names."
            )
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
        else:
            notes.append("Text extracted via OCR (Tesseract). Accuracy depends on image quality and font clarity.")

    # Token efficiency note
    if ctx.original_tokens > 0 and ctx.manifest:
        saved = ctx.original_tokens - ctx.manifest.optimized_tokens
        if saved > 0:
            pct = round(saved / ctx.original_tokens * 100)
            notes.append(f"Optimiser removed {saved:,} redundant tokens ({pct}%) - headers, footers, duplicates.")

    return ConfidenceResult(score=max(0, min(100, score)), notes=notes)


def compute_readiness_score(ctx: CompilationContext) -> int:
    """Backwards-compatible entry point. Returns integer score only."""
    return compute_confidence(ctx).score
