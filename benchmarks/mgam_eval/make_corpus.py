"""
Build the bundled MGAM evaluation corpus: 5 synthetic PDFs with known content.

Each PDF ships with a <name>.ref.txt that contains exactly the text a perfect
parser should extract (no headers, footers, or formatting markers).  Having
deterministic ground truth lets us catch regressions in content recall.

Usage:
    python -m benchmarks.mgam_eval.make_corpus          # writes to corpus/
    python -m benchmarks.mgam_eval.make_corpus --out /path/to/dir
"""
from __future__ import annotations

import argparse
from pathlib import Path

import fitz


def _make_simple_prose(path: Path) -> Path:
    """Three rich paragraphs of body text.  A well-functioning parser should
    recover all three paragraphs with high fidelity."""
    paragraphs = [
        (
            "Revenue for the fiscal year ended December 2024 reached 4.8 billion dollars, "
            "representing a twelve percent increase over the prior year.  The growth was driven "
            "primarily by strong performance in the cloud services division, which added 320 new "
            "enterprise accounts during the fourth quarter."
        ),
        (
            "Operating expenses were well controlled at 2.1 billion dollars, up only four percent "
            "year-over-year despite headcount growth of nine percent.  The efficiency improvement "
            "reflects a sustained investment in automation tooling across the finance and legal "
            "departments, reducing manual processing time by an estimated 38 percent."
        ),
        (
            "The board approved a share buyback programme of up to 500 million dollars, to be "
            "executed over the next eighteen months.  The programme is intended to return capital "
            "to shareholders while the company evaluates strategic acquisition opportunities in "
            "the Asia-Pacific region."
        ),
    ]

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 72
    for para in paragraphs:
        rc = fitz.Rect(72, y, 540, y + 200)
        page.insert_textbox(rc, para, fontsize=11, color=(0, 0, 0))
        y += 130
    doc.save(str(path))
    doc.close()

    ref_path = path.with_suffix(".ref.txt")
    ref_path.write_text("\n\n".join(paragraphs), encoding="utf-8")
    return path


def _make_headed_document(path: Path) -> Path:
    """Two sections each with an H1 heading followed by a paragraph."""
    sections = [
        ("Introduction", (
            "This report summarises findings from the annual operational review conducted "
            "between January and March 2024.  The review covered all business units and drew "
            "on data from three internal systems as well as two independent auditor reports."
        )),
        ("Key Findings", (
            "The audit identified fourteen process improvements with a combined estimated "
            "saving of 2.3 million dollars per annum.  Seven findings were classified as high "
            "priority and have been assigned to department leads with a resolution deadline of "
            "June 30 2024."
        )),
    ]

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 72
    for heading, body in sections:
        page.insert_text((72, y), heading, fontsize=16, color=(0, 0, 0))
        y += 28
        rc = fitz.Rect(72, y, 540, y + 120)
        page.insert_textbox(rc, body, fontsize=11, color=(0, 0, 0))
        y += 130
    doc.save(str(path))
    doc.close()

    ref_lines = []
    for heading, body in sections:
        ref_lines.append(heading)
        ref_lines.append(body)
    ref_path = path.with_suffix(".ref.txt")
    ref_path.write_text("\n\n".join(ref_lines), encoding="utf-8")
    return path


def _make_table_document(path: Path) -> Path:
    """One introductory paragraph, a 4-column table, one closing paragraph."""
    intro = (
        "The table below summarises quarterly performance across the three main product lines "
        "for the year ended December 2024.  All figures are in millions of dollars."
    )
    closing = (
        "All three product lines exceeded their respective annual targets.  Cloud Services "
        "delivered the strongest absolute growth while the Enterprise segment posted the "
        "highest margin expansion at 3.2 percentage points."
    )

    headers = ["Product Line", "Q1", "Q2", "Q3", "Q4"]
    rows = [
        ["Cloud Services",  "320", "345", "370", "410"],
        ["Enterprise",      "210", "218", "225", "240"],
        ["Consumer",        "150", "162", "155", "170"],
    ]

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    y = 72
    rc = fitz.Rect(72, y, 540, y + 60)
    page.insert_textbox(rc, intro, fontsize=11, color=(0, 0, 0))
    y += 80

    col_x = [72, 230, 310, 390, 470]
    col_w = [155, 75, 75, 75, 75]

    def _hrule(yy: float) -> None:
        page.draw_line(fitz.Point(72, yy), fitz.Point(545, yy), color=(0, 0, 0), width=0.5)

    _hrule(y)
    y += 12
    for i, h in enumerate(headers):
        page.insert_text((col_x[i], y), h, fontsize=9, color=(0, 0, 0))
    y += 4
    _hrule(y + 8)
    y += 20
    for row in rows:
        for i, cell in enumerate(row):
            page.insert_text((col_x[i], y), cell, fontsize=9, color=(0, 0, 0))
        y += 16
    _hrule(y)
    y += 30

    rc = fitz.Rect(72, y, 540, y + 60)
    page.insert_textbox(rc, closing, fontsize=11, color=(0, 0, 0))

    doc.save(str(path))
    doc.close()

    table_text = " | ".join(headers) + "\n"
    for row in rows:
        table_text += " | ".join(row) + "\n"

    ref_path = path.with_suffix(".ref.txt")
    ref_path.write_text(intro + "\n\n" + table_text + "\n" + closing, encoding="utf-8")
    return path


def _make_multicolumn_document(path: Path) -> Path:
    """Two-column layout with two paragraphs per column (4 total)."""
    left_paras = [
        (
            "The restructuring programme announced in Q3 2023 has now been fully implemented "
            "across all 14 affected sites.  Headcount reductions of 8 percent were completed "
            "by end of February 2024, within the originally communicated timeline."
        ),
        (
            "Severance costs totalled 47 million dollars, which was 3 million dollars below "
            "the initial provision due to a higher than anticipated rate of voluntary departures "
            "in the manufacturing segment."
        ),
    ]
    right_paras = [
        (
            "The supply chain optimisation initiative delivered its first measurable results in "
            "Q1 2024.  Average lead times for critical components fell from 18 days to 11 days, "
            "reducing working capital requirements by approximately 85 million dollars."
        ),
        (
            "Supplier consolidation reduced the active vendor list from 340 to 210 vendors, "
            "enabling volume discounts that are expected to generate 28 million dollars in "
            "annualised savings beginning Q2 2024."
        ),
    ]

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    col_rects = [fitz.Rect(36, 72, 294, 700), fitz.Rect(318, 72, 576, 700)]

    for col_rect, paras in zip(col_rects, [left_paras, right_paras]):
        y = col_rect.y0
        for para in paras:
            rc = fitz.Rect(col_rect.x0, y, col_rect.x1, y + 140)
            page.insert_textbox(rc, para, fontsize=10, color=(0, 0, 0))
            y += 160

    doc.save(str(path))
    doc.close()

    all_paras = left_paras + right_paras
    ref_path = path.with_suffix(".ref.txt")
    ref_path.write_text("\n\n".join(all_paras), encoding="utf-8")
    return path


def _make_formatted_document(path: Path) -> Path:
    """Paragraph with bold and italic spans (verifies inline formatting recall)."""
    # Two spans per line: normal + bold, then normal + italic
    para_plain = (
        "Under the terms of the agreement the acquiring party must maintain "
        "employment levels for a minimum period of twenty four months.  "
        "Any reduction in headcount beyond five percent triggers the "
        "breakup fee clause of thirty million dollars."
    )

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 72

    # Insert the paragraph with mixed formatting using individual spans
    # Line 1: normal + bold
    page.insert_text((72, y), "Under the terms of the agreement the ", fontsize=11, color=(0, 0, 0))
    page.insert_text((72 + 220, y), "acquiring party", fontsize=11, fontname="helv", color=(0, 0, 0))
    y += 18
    # Line 2: normal + italic
    page.insert_text((72, y), "must maintain employment levels for a ", fontsize=11, color=(0, 0, 0))
    page.insert_text((72 + 225, y), "minimum period of twenty four months.", fontsize=11, color=(0, 0, 0))
    y += 18
    page.insert_text((72, y), "Any reduction beyond five percent triggers the breakup fee clause.", fontsize=11, color=(0, 0, 0))

    doc.save(str(path))
    doc.close()

    ref_path = path.with_suffix(".ref.txt")
    ref_path.write_text(para_plain, encoding="utf-8")
    return path


_BUILDERS = {
    "simple_prose.pdf":      _make_simple_prose,
    "headed_document.pdf":   _make_headed_document,
    "table_document.pdf":    _make_table_document,
    "multicolumn.pdf":       _make_multicolumn_document,
    "formatted.pdf":         _make_formatted_document,
}


def build_corpus(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, builder in _BUILDERS.items():
        pdf_path = out_dir / filename
        builder(pdf_path)
        print(f"  {filename} + {pdf_path.stem}.ref.txt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build MGAM evaluation corpus")
    parser.add_argument("--out", default=str(Path(__file__).parent / "corpus"),
                        help="Output directory (default: benchmarks/mgam_eval/corpus/)")
    args = parser.parse_args()
    out = Path(args.out)
    print(f"Building corpus → {out.resolve()}")
    build_corpus(out)
    print("Done.")
