"""PDF regression fixtures — synthetic-but-realistic PDFs compiled end-to-end.

Each fixture is built with PyMuPDF (fitz) and exercises a distinct PDF archetype:
  1. academic_multicol  — two-column layout with headings and references
  2. scanned_image_only — image-only pages with no text layer (no OCR)
  3. financial_tables   — dense tables with a sparse cover page
  4. slide_export       — large centered titles, bullet paragraphs
  5. technical_sheet    — prose + a spec table

All asserts cover: output not empty, key text present, page coverage reasonable,
headings/tables preserved where applicable, readiness score drops on bad extraction.
"""
from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from PIL import Image, ImageDraw

from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType
from aksharamd.scoring.readiness import compute_confidence

# ── helpers ───────────────────────────────────────────────────────────────────

def _compile(pdf_path: Path, tmp_path: Path):
    """Compile a PDF and return (markdown_text, ctx)."""
    compiler = Compiler(output_dir=str(tmp_path / "out"))
    return compiler.compile_to_string(str(pdf_path))


def _block_types(ctx) -> list[str]:
    return [b.type.value for b in ctx.document.blocks] if ctx.document else []


def _all_text(ctx) -> str:
    if not ctx.document:
        return ""
    return " ".join(b.content for b in ctx.document.blocks)


def _readiness(ctx) -> int:
    return compute_confidence(ctx).score


# ── fixture builders ──────────────────────────────────────────────────────────

def _build_academic_multicol(path: Path) -> None:
    """Two-column academic paper: title, abstract, two sections with body text."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    # Title — large centered
    page.insert_text((100, 60), "Indoor Air Quality in Multi-Zone Buildings",
                     fontsize=18, color=(0, 0, 0))

    # Authors line
    page.insert_text((150, 88), "Smith, J.; Patel, R.; Zhang, L.",
                     fontsize=11, color=(0, 0, 0))

    # Abstract heading
    page.insert_text((220, 115), "Abstract", fontsize=12, color=(0, 0, 0))

    # Abstract body (full-width)
    abstract = (
        "This paper presents a measurement campaign in six office buildings "
        "to characterize CO2, PM2.5, and TVOC concentrations. We find that "
        "mechanical ventilation systems operating at design flow rates maintain "
        "acceptable IAQ in 83% of monitored zones during occupied hours."
    )
    _insert_wrapped(page, abstract, x=72, y=135, width=468, fontsize=10)

    # Column 1 — Introduction
    page.insert_text((72, 220), "1. Introduction", fontsize=14, color=(0, 0, 0))
    col1_body = (
        "Indoor air quality (IAQ) has been shown to directly affect occupant "
        "productivity and health outcomes. ASHRAE Standard 62.1 specifies minimum "
        "ventilation rates for acceptable IAQ. However, compliance monitoring in "
        "existing buildings remains limited due to the cost of continuous sensors."
    )
    _insert_wrapped(page, col1_body, x=72, y=238, width=230, fontsize=10)

    page.insert_text((72, 340), "2. Methods", fontsize=14, color=(0, 0, 0))
    col1_methods = (
        "Measurement equipment included HOBO MX1102A CO2 loggers and "
        "Plantower PMS5003 particle sensors. Data were logged at 1-minute "
        "intervals over a 4-week period in heating season."
    )
    _insert_wrapped(page, col1_methods, x=72, y=358, width=230, fontsize=10)

    # Column 2 — Results
    page.insert_text((320, 220), "3. Results", fontsize=14, color=(0, 0, 0))
    col2_body = (
        "Mean CO2 concentration across all zones was 842 ppm (SD=187 ppm). "
        "Peak concentrations exceeded 1200 ppm in 31% of zones during morning "
        "occupancy surges. PM2.5 remained below 12 μg/m³ in all monitored zones "
        "except the copy room (mean 28 μg/m³). TVOC levels were generally below "
        "WHO guidelines of 300 μg/m³."
    )
    _insert_wrapped(page, col2_body, x=320, y=238, width=230, fontsize=10)

    page.insert_text((320, 390), "4. Conclusions", fontsize=14, color=(0, 0, 0))
    col2_concl = (
        "Mechanical ventilation at ASHRAE 62.1 design rates is sufficient for "
        "acceptable CO2 levels in standard office occupancies. Copy and print "
        "rooms require supplemental local exhaust to manage particle loads."
    )
    _insert_wrapped(page, col2_concl, x=320, y=408, width=230, fontsize=10)

    # References section
    page.insert_text((72, 520), "References", fontsize=14, color=(0, 0, 0))
    refs = [
        "[1] ASHRAE Standard 62.1-2022. Ventilation and Acceptable Indoor Air Quality.",
        "[2] Fisk, W.J. (2017). Review of IAQ interventions. Indoor Air, 27(1), 7-25.",
        "[3] Zhang, L. et al. (2021). CO2 as IAQ proxy. Build. Environ., 195, 107-112.",
    ]
    y = 538
    for ref in refs:
        _insert_wrapped(page, ref, x=72, y=y, width=468, fontsize=9)
        y += 22

    doc.save(str(path))
    doc.close()


def _build_scanned_image_only(path: Path) -> None:
    """3-page PDF where each page is a rasterized JPEG image — no text layer."""
    doc = fitz.open()
    for page_num in range(1, 4):
        # Render synthetic "scanned page" as a PIL image
        img = Image.new("RGB", (612, 792), color=(245, 245, 240))
        draw = ImageDraw.Draw(img)
        # Fake text lines as gray rectangles (no actual text layer)
        draw.text((80, 60), f"SCANNED PAGE {page_num}", fill=(30, 30, 30))
        draw.text((80, 90), "This text exists only as pixels — no PDF text layer.", fill=(50, 50, 50))
        for line_y in range(130, 700, 24):
            draw.line([(80, line_y), (530, line_y + 4)], fill=(80, 80, 80), width=1)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)

        page = doc.new_page(width=612, height=792)
        rect = fitz.Rect(0, 0, 612, 792)
        page.insert_image(rect, stream=buf.read())

    doc.save(str(path))
    doc.close()


def _build_financial_tables(path: Path) -> None:
    """4-page PDF: sparse cover page + 3 pages of dense tables."""
    doc = fitz.open()

    # Page 1: sparse cover
    cover = doc.new_page(width=612, height=792)
    cover.insert_text((180, 300), "Annual Financial Report", fontsize=22, color=(0, 0, 0))
    cover.insert_text((220, 335), "Fiscal Year 2023", fontsize=14, color=(0, 0, 0))
    cover.insert_text((210, 360), "Prepared by Finance Team", fontsize=11, color=(0.4, 0.4, 0.4))

    # Pages 2-4: tables
    for pg in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 50), f"Table {pg + 1}: Revenue by Quarter", fontsize=13, color=(0, 0, 0))

        # Draw table grid
        headers = ["Quarter", "Revenue ($K)", "Expenses ($K)", "Net ($K)", "YoY %"]
        rows = [
            ["Q1", "4,821", "3,104", "1,717", "+12.3%"],
            ["Q2", "5,340", "3,290", "2,050", "+18.6%"],
            ["Q3", "5,102", "3,415", "1,687", "+8.1%"],
            ["Q4", "6,205", "3,890", "2,315", "+22.4%"],
            ["Total", "21,468", "13,699", "7,769", "+15.2%"],
        ]
        col_x = [72, 190, 300, 400, 490]

        y = 75
        # Header row
        for i, hdr in enumerate(headers):
            page.insert_text((col_x[i] + 4, y + 12), hdr, fontsize=9, color=(0, 0, 0))
        page.draw_rect(fitz.Rect(72, y, 572, y + 18), color=(0, 0, 0), width=0.5)
        y += 18

        for row in rows:
            for i, cell in enumerate(row):
                page.insert_text((col_x[i] + 4, y + 12), cell, fontsize=9, color=(0, 0, 0))
            page.draw_rect(fitz.Rect(72, y, 572, y + 16), color=(0.7, 0.7, 0.7), width=0.3)
            y += 16

        # Add some prose below
        page.insert_text((72, y + 20), "Notes:", fontsize=10, color=(0, 0, 0))
        note_text = (
            "All figures are in thousands USD. YoY % calculated vs prior fiscal year. "
            "Q4 revenue includes one-time licence fees of $340K."
        )
        _insert_wrapped(page, note_text, x=72, y=y + 36, width=468, fontsize=9)

    doc.save(str(path))
    doc.close()


def _build_slide_export(path: Path) -> None:
    """4-page slide-export PDF: large centered titles, bulleted paragraphs."""
    doc = fitz.open()
    slides = [
        {
            "title": "AksharaMD: AI Document Compiler",
            "subtitle": "Transforms documents for LLM consumption",
            "bullets": [
                "Supports PDF, DOCX, PPTX, XLSX, HTML, and 40+ formats",
                "Extracts structured blocks: headings, tables, code, images",
                "Quality validation with extraction confidence scoring",
                "MCP server integration for Claude and other AI assistants",
            ],
        },
        {
            "title": "Architecture Overview",
            "subtitle": "Pipeline stages",
            "bullets": [
                "Parse: format-specific extraction into Block model",
                "Clean: deduplication, furniture removal, normalisation",
                "Optimise: token counting and redundancy reduction",
                "Validate: structural checks and quality signals",
                "Export: markdown, JSON, multimodal content arrays",
            ],
        },
        {
            "title": "PDF Extraction Challenges",
            "subtitle": "Why PDFs are hard",
            "bullets": [
                "No universal structure — layout is entirely positional",
                "Scanned pages require OCR (pytesseract)",
                "Multi-column layouts require column boundary detection",
                "CID font artifacts can garble extracted text",
                "Page furniture (headers, footers, page numbers) must be removed",
            ],
        },
        {
            "title": "Quality Signals",
            "subtitle": "How AksharaMD detects bad extractions",
            "bullets": [
                "NEAR_EMPTY_OUTPUT: fewer than 80 chars/page extracted",
                "LOW_TEXT_DENSITY: PDF appears image-heavy",
                "GLYPH_ARTIFACTS: CID font encoding problems detected",
                "OCR_REQUIRED: scanned pages found but OCR not installed",
                "TOKEN_BLOAT: suspiciously high tokens per page",
            ],
        },
    ]
    for slide in slides:
        page = doc.new_page(width=960, height=540)
        # Title
        page.insert_text((100, 80), slide["title"], fontsize=28, color=(0.1, 0.1, 0.5))
        # Subtitle
        page.insert_text((100, 125), slide["subtitle"], fontsize=16, color=(0.3, 0.3, 0.3))
        # Bullet points
        y = 175
        for bullet in slide["bullets"]:
            page.insert_text((120, y), f"• {bullet}", fontsize=13, color=(0.1, 0.1, 0.1))
            y += 38

    doc.save(str(path))
    doc.close()


def _build_technical_sheet(path: Path) -> None:
    """2-page technical datasheet: introduction prose + specifications table."""
    doc = fitz.open()

    # Page 1: product description
    page1 = doc.new_page(width=612, height=792)
    page1.insert_text((72, 55), "HVACPro 3000 Series Air Handler", fontsize=18, color=(0, 0, 0))
    page1.insert_text((72, 82), "Technical Datasheet — Rev. 4.2", fontsize=11, color=(0.4, 0.4, 0.4))

    page1.insert_text((72, 115), "Product Overview", fontsize=15, color=(0, 0, 0))
    overview = (
        "The HVACPro 3000 Series is a modular air handling unit designed for commercial "
        "applications requiring precise temperature and humidity control. The unit features "
        "an EC motor drive, MERV-13 filtration, and integrated demand-controlled ventilation "
        "compatible with BACnet/IP and Modbus RTU protocols."
    )
    _insert_wrapped(page1, overview, x=72, y=133, width=468, fontsize=10)

    page1.insert_text((72, 220), "Features", fontsize=15, color=(0, 0, 0))
    features = [
        "Airflow range: 500–8,000 CFM (236–3,776 L/s)",
        "Operating temperature: -20°F to 125°F (-29°C to 52°C)",
        "Filtration: MERV-13 standard; HEPA optional upgrade",
        "Variable frequency drive: 0–60 Hz, ±1% accuracy",
        "Sound level: ≤55 dB(A) at 1m in standard configuration",
        "Energy recovery: optional enthalpy wheel, ≥70% sensible efficiency",
    ]
    y = 240
    for feat in features:
        page1.insert_text((88, y), f"- {feat}", fontsize=10, color=(0, 0, 0))
        y += 18

    page1.insert_text((72, y + 15), "Compliance", fontsize=15, color=(0, 0, 0))
    compliance = (
        "Unit complies with ASHRAE Standard 90.1-2022, AHRI Standard 430, "
        "and UL 1995. CE marking applicable for European installations. "
        "ETL listed per UL 1995 for US and Canada."
    )
    _insert_wrapped(page1, compliance, x=72, y=y + 33, width=468, fontsize=10)

    # Page 2: specifications table
    page2 = doc.new_page(width=612, height=792)
    page2.insert_text((72, 50), "Electrical Specifications", fontsize=15, color=(0, 0, 0))

    spec_headers = ["Parameter", "Value", "Unit", "Notes"]
    spec_rows = [
        ["Supply voltage", "208 / 230 / 460", "VAC", "3-phase, 60 Hz"],
        ["Full load current", "18.5 / 17.2 / 9.1", "A", "At rated airflow"],
        ["Min circuit ampacity", "23", "A", "NEC 440.22"],
        ["Max fuse size", "35", "A", "HACR type"],
        ["Control voltage", "24", "VAC", "Class 2 transformer"],
        ["Standby power", "< 5", "W", "BACnet controller active"],
        ["Power factor", "≥ 0.95", "—", "At full load"],
        ["Inrush current", "≤ 6×FLA", "A", "Soft-start standard"],
    ]
    col_x2 = [72, 230, 355, 420]

    y = 70
    for i, hdr in enumerate(spec_headers):
        page2.insert_text((col_x2[i] + 3, y + 12), hdr, fontsize=10, color=(0, 0, 0))
    page2.draw_rect(fitz.Rect(72, y, 560, y + 18), color=(0, 0, 0), width=0.5)
    y += 18

    for row in spec_rows:
        for i, cell in enumerate(row):
            page2.insert_text((col_x2[i] + 3, y + 12), cell, fontsize=9.5, color=(0, 0, 0))
        page2.draw_rect(fitz.Rect(72, y, 560, y + 16), color=(0.8, 0.8, 0.8), width=0.3)
        y += 16

    page2.insert_text((72, y + 20), "Dimensional Data", fontsize=15, color=(0, 0, 0))
    dim_text = (
        "Unit dimensions (W × H × D): 48″ × 54″ × 36″ (1219 × 1372 × 914 mm). "
        "Operating weight: 680 lb (309 kg). Shipping weight: 740 lb (336 kg). "
        "Clearance requirements: 36″ front, 18″ sides and rear (per NFPA 70)."
    )
    _insert_wrapped(page2, dim_text, x=72, y=y + 38, width=468, fontsize=10)

    doc.save(str(path))
    doc.close()


def _build_narrow_booktabs_table(path: Path) -> None:
    """Single-page PDF with a narrow booktabs-style table.

    Column text left-edges are 15 pt apart — below the old hardcoded 20 pt
    gap threshold (which would yield n_cols=1 and reject the table) but above
    the adaptive threshold for a 308 pt-wide table: max(8, 308*0.04)=12.3 pt.
    Three thin-rect horizontal rules simulate booktabs top/mid/bottom rules.
    """
    doc = fitz.open()
    page = doc.new_page(width=612, height=300)

    page.insert_text((72, 35), "Quarterly Performance", fontsize=11, color=(0, 0, 0))

    col_x = [75, 90, 105, 120]  # left-edge gaps: 15 pt
    headers = ["Metric", "Q1", "Q2", "Q3"]
    data_rows_content = [
        ["Revenue", "100", "120", "140"],
        ["Costs",   "80",  "90",  "100"],
        ["Net",     "20",  "30",  "40"],
    ]

    def _hrule(y: float) -> None:
        # Height=1 pt satisfies r.height <= 3 in _try_hrule_table "re" check.
        # Width=308 pt (72→380) satisfies r.width >= page_width*0.20 = 122.4 pt.
        page.draw_rect(fitz.Rect(72, y, 380, y + 1), color=(0, 0, 0), fill=(0, 0, 0))

    y = 50
    _hrule(y)          # top rule
    y += 10
    for i, hdr in enumerate(headers):
        page.insert_text((col_x[i], y), hdr, fontsize=9, color=(0, 0, 0))
    y += 14
    _hrule(y)          # mid rule (after header)
    y += 10
    for row in data_rows_content:
        for i, cell in enumerate(row):
            page.insert_text((col_x[i], y), cell, fontsize=9, color=(0, 0, 0))
        y += 14
    _hrule(y)          # bottom rule

    doc.save(str(path))
    doc.close()


def _insert_wrapped(page, text: str, x: float, y: float, width: float, fontsize: float) -> None:
    """Insert text with naive word-wrap into a fitz page."""
    words = text.split()
    line = ""
    line_h = fontsize * 1.4
    for word in words:
        test = (line + " " + word).strip()
        # Approximate: 0.55 × fontsize per char
        if len(test) * fontsize * 0.55 > width and line:
            page.insert_text((x, y), line, fontsize=fontsize, color=(0, 0, 0))
            y += line_h
            line = word
        else:
            line = test
    if line:
        page.insert_text((x, y), line, fontsize=fontsize, color=(0, 0, 0))


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pdf_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("pdf_fixtures")
    _build_academic_multicol(d / "academic_multicol.pdf")
    _build_scanned_image_only(d / "scanned_image_only.pdf")
    _build_financial_tables(d / "financial_tables.pdf")
    _build_slide_export(d / "slide_export.pdf")
    _build_technical_sheet(d / "technical_sheet.pdf")
    _build_narrow_booktabs_table(d / "narrow_booktabs.pdf")
    return d


# ── 1. Academic multi-column ──────────────────────────────────────────────────

def test_academic_output_not_empty(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "academic_multicol.pdf", tmp_path)
    assert text.strip(), "Academic PDF produced no output"


def test_academic_key_text_present(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "academic_multicol.pdf", tmp_path)
    all_text = _all_text(ctx)
    assert "Indoor Air Quality" in all_text or "IAQ" in all_text
    assert "ASHRAE" in all_text


def test_academic_headings_extracted(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "academic_multicol.pdf", tmp_path)
    heading_blocks = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    # Two-column layouts may merge section headings into adjacent paragraphs;
    # assert at minimum the document title is captured as a heading.
    assert len(heading_blocks) >= 1, "Expected at least 1 heading in academic PDF"
    all_text = _all_text(ctx)
    assert "Introduction" in all_text or "Methods" in all_text or "Results" in all_text


def test_academic_readiness_score_reasonable(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "academic_multicol.pdf", tmp_path)
    score = _readiness(ctx)
    assert score >= 50, f"Academic PDF readiness score too low: {score}"


# ── 2. Scanned image-only ─────────────────────────────────────────────────────

def test_scanned_output_has_notice_or_low_content(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "scanned_image_only.pdf", tmp_path)
    # Without OCR the document should either be near-empty or surface a notice
    all_text = _all_text(ctx).lower()
    pages_with_blocks = {b.page for b in ctx.document.blocks if b.page is not None}
    # Either: very little text was extracted, or a notice about OCR/images appears
    short_extraction = sum(len(b.content) for b in ctx.document.blocks) < 500
    has_notice = "image" in all_text or "ocr" in all_text or not pages_with_blocks
    assert short_extraction or has_notice, (
        "Scanned PDF extraction should be minimal or include an OCR notice"
    )


def test_scanned_readiness_score_lower_than_academic(pdf_dir, tmp_path):
    _, scanned_ctx = _compile(pdf_dir / "scanned_image_only.pdf", tmp_path / "s1")
    _, academic_ctx = _compile(pdf_dir / "academic_multicol.pdf", tmp_path / "s2")
    scanned_score = _readiness(scanned_ctx)
    academic_score = _readiness(academic_ctx)
    assert scanned_score < academic_score, (
        f"Scanned PDF score ({scanned_score}) should be below academic ({academic_score})"
    )


def test_scanned_does_not_crash(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "scanned_image_only.pdf", tmp_path)
    assert ctx is not None
    assert ctx.document is not None


# ── 3. Financial tables ───────────────────────────────────────────────────────

def test_financial_output_not_empty(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "financial_tables.pdf", tmp_path)
    assert text.strip(), "Financial PDF produced no output"


def test_financial_key_text_present(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "financial_tables.pdf", tmp_path)
    all_text = _all_text(ctx)
    assert "Revenue" in all_text or "revenue" in all_text
    assert "Annual Financial Report" in all_text or "Financial" in all_text


def test_financial_page_coverage_reasonable(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "financial_tables.pdf", tmp_path)
    pages_with_blocks = {b.page for b in ctx.document.blocks if b.page is not None}
    assert len(pages_with_blocks) >= 2, (
        f"Expected blocks on at least 2 pages, got: {pages_with_blocks}"
    )


def test_financial_readiness_score_reasonable(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "financial_tables.pdf", tmp_path)
    score = _readiness(ctx)
    assert score >= 45, f"Financial PDF readiness score too low: {score}"


# ── 4. Slide export ───────────────────────────────────────────────────────────

def test_slides_output_not_empty(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "slide_export.pdf", tmp_path)
    assert text.strip(), "Slide export PDF produced no output"


def test_slides_key_text_present(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "slide_export.pdf", tmp_path)
    all_text = _all_text(ctx)
    assert "AksharaMD" in all_text or "Pipeline" in all_text or "Document" in all_text


def test_slides_headings_extracted(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "slide_export.pdf", tmp_path)
    heading_blocks = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(heading_blocks) >= 2, (
        f"Expected at least 2 headings from slide deck, got {len(heading_blocks)}"
    )


def test_slides_page_coverage_all_slides(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "slide_export.pdf", tmp_path)
    pages_with_blocks = {b.page for b in ctx.document.blocks if b.page is not None}
    # 4 slides — should get content from at least 3
    assert len(pages_with_blocks) >= 3, (
        f"Expected coverage of ≥3 slide pages, got: {pages_with_blocks}"
    )


def test_slides_readiness_score_reasonable(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "slide_export.pdf", tmp_path)
    score = _readiness(ctx)
    assert score >= 50, f"Slide export readiness score too low: {score}"


# ── 5. Technical datasheet ────────────────────────────────────────────────────

def test_datasheet_output_not_empty(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "technical_sheet.pdf", tmp_path)
    assert text.strip(), "Technical datasheet produced no output"


def test_datasheet_key_text_present(pdf_dir, tmp_path):
    text, ctx = _compile(pdf_dir / "technical_sheet.pdf", tmp_path)
    all_text = _all_text(ctx)
    assert "HVACPro" in all_text or "MERV" in all_text or "ventilation" in all_text.lower()


def test_datasheet_both_pages_covered(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "technical_sheet.pdf", tmp_path)
    pages_with_blocks = {b.page for b in ctx.document.blocks if b.page is not None}
    assert len(pages_with_blocks) >= 2, (
        f"Expected blocks on both pages, got: {pages_with_blocks}"
    )


def test_datasheet_headings_extracted(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "technical_sheet.pdf", tmp_path)
    heading_blocks = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(heading_blocks) >= 2, (
        f"Expected at least 2 headings in datasheet, got {len(heading_blocks)}"
    )


def test_datasheet_readiness_score_reasonable(pdf_dir, tmp_path):
    _, ctx = _compile(pdf_dir / "technical_sheet.pdf", tmp_path)
    score = _readiness(ctx)
    assert score >= 50, f"Technical datasheet readiness score too low: {score}"


# ── Cross-fixture: bad extractions score lower than good ones ─────────────────

def test_scanned_scores_worst_of_five(pdf_dir, tmp_path):
    """Scanned (no-text-layer) PDF should score lowest among the five fixtures."""
    results = {}
    for name in ["academic_multicol", "scanned_image_only", "financial_tables",
                 "slide_export", "technical_sheet"]:
        _, ctx = _compile(pdf_dir / f"{name}.pdf", tmp_path / name)
        results[name] = _readiness(ctx)

    scanned = results["scanned_image_only"]
    others = {k: v for k, v in results.items() if k != "scanned_image_only"}
    worse_than_all = all(scanned < v for v in others.values())
    assert worse_than_all, (
        f"Scanned PDF ({scanned}) should score lower than all text-bearing PDFs: {others}"
    )


# ── 6. Narrow booktabs table (adaptive column gap) ───────────────────────────

def test_narrow_booktabs_columns_detected(pdf_dir, tmp_path):
    """Adaptive column gap allows tables with 15 pt column spacing to be detected.

    With the old hardcoded 20 pt threshold all column boundaries were missed
    (n_cols=1), causing the table to be rejected.  The adaptive threshold
    (4% of table width, ≥8 pt) gives ~12.3 pt for this 308 pt-wide table,
    which correctly finds all four column boundaries.
    """
    _, ctx = _compile(pdf_dir / "narrow_booktabs.pdf", tmp_path)
    table_blocks = [b for b in ctx.document.blocks if b.type == BlockType.TABLE]
    assert table_blocks, "Narrow booktabs table should be detected as a TABLE block"
    table_text = " ".join(b.content for b in table_blocks)
    assert "Q1" in table_text, f"Column header 'Q1' missing from table: {table_text!r}"
    assert "Metric" in table_text, f"Column header 'Metric' missing from table: {table_text!r}"
