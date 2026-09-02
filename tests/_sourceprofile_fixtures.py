"""Generate PDF fixtures that exercise the `pages_containing_tables` risky
field the SourceProfile refactor must preserve.

The parsebench dev split is a single-page-per-doc corpus, so both cases the
design's Section 2.3 flags — multi-table pages and stitched cross-page
tables — are structurally unreachable. This script builds two synthetic
PDFs so the byte-identity gate has something to gate on:

  * ``multi_table_page.pdf``
    One page, two tables. Should register ``pdf_stats["table_pages"] = 1``
    and ``table_count = 2``. Divergence: 1 vs 2.

  * ``stitched_table_across_pages.pdf``
    Multi-page (3 pages) with an identical table header + continuation
    rows on each page. Empirically produces ``pdf_stats["table_pages"] = 3``
    and ``table_count = 2`` — a divergence that catches both naive
    (len(blocks)) and tighter (len({b.page})) block derivations.

The generator writes to a caller-supplied directory. This module is
imported by ``tools/analysis/sourceprofile_baseline.py`` and by
``tests/test_source_profile_adapter.py`` so both produce the same fixture
bytes at runtime without ever checking a PDF into git (repo policy
enforced by ``tests/test_parsebench_page_ground_truth.py``).

Deterministic: PyMuPDF drawing has no randomness in this script, so the
same generator inputs produce byte-identical PDFs across runs.

**Gate integrity depends on this determinism.** The Step 4 byte-identity
gate compares SourceProfile refactor output against a baseline JSON that
was captured against these exact fixture bytes. If a future PyMuPDF
version upgrade changes emitted PDF bytes (e.g. compression settings,
font subsetting, or object ordering), the pipeline output on the
regenerated fixtures may drift — and that drift will look like a scoring
regression at the gate when it's really a PyMuPDF drift. If the gate
fails only on the fixture rows, verify PyMuPDF hasn't upgraded before
concluding the refactor is at fault. Pin the version in ``pyproject.toml``
if this becomes a recurring issue.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pymupdf


class FixturePaths(NamedTuple):
    multi_table_page: Path
    stitched_table_across_pages: Path

# ── Shared geometry helpers ───────────────────────────────────────────────────

def _draw_grid_table(page: pymupdf.Page, x0: float, y0: float,
                     rows: list[list[str]], col_width: float = 90,
                     row_height: float = 20) -> None:
    """Draw a simple grid table with borders and text at (x0, y0)."""
    n_rows = len(rows)
    n_cols = max(len(r) for r in rows) if rows else 0
    if n_rows == 0 or n_cols == 0:
        return
    total_w = col_width * n_cols
    total_h = row_height * n_rows
    # Horizontal lines
    for i in range(n_rows + 1):
        y = y0 + i * row_height
        page.draw_line((x0, y), (x0 + total_w, y), width=0.7)
    # Vertical lines
    for j in range(n_cols + 1):
        x = x0 + j * col_width
        page.draw_line((x, y0), (x, y0 + total_h), width=0.7)
    # Cell text (centered-ish)
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            tx = x0 + j * col_width + 6
            ty = y0 + i * row_height + row_height * 0.65
            page.insert_text((tx, ty), cell, fontsize=10)


# ── Fixture 1: multi-table page ───────────────────────────────────────────────

def make_multi_table_page(out_dir: Path) -> Path:
    """One page, two distinct tables. Should yield table_pages=1, table_count=2."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "multi_table_page.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)  # US Letter

    # Preface prose so table extraction has some context.
    page.insert_text((72, 72), "Quarterly Summary Report",
                     fontsize=14, fontname="helv")
    page.insert_text((72, 100),
                     "The following two tables summarize activity for Q1 and Q2.",
                     fontsize=11)

    # Table A near top
    page.insert_text((72, 140), "Table A: Q1 metrics", fontsize=11)
    _draw_grid_table(
        page, 72, 150,
        [
            ["Region",    "Units",  "Revenue"],
            ["North",     "1200",   "48000"],
            ["South",     "980",    "39200"],
            ["East",      "1420",   "56800"],
        ],
    )

    # Filler prose between the two tables so pdfplumber doesn't merge them.
    page.insert_text((72, 300), "Q2 results follow, in a separate table.", fontsize=11)

    # Table B lower on the page
    page.insert_text((72, 340), "Table B: Q2 metrics", fontsize=11)
    _draw_grid_table(
        page, 72, 350,
        [
            ["Region",    "Units",  "Revenue"],
            ["North",     "1310",   "52400"],
            ["South",     "1050",   "42000"],
            ["East",      "1490",   "59600"],
        ],
    )

    doc.save(out)
    doc.close()
    return out


# ── Fixture 2: stitched cross-page table ──────────────────────────────────────

def make_stitched_cross_page_table(out_dir: Path) -> Path:
    """Three pages with the same table header + continuation rows on each.

    Whether pdf_tables/stitching.py actually stitches these into a single
    Block depends on the extractor's heuristics — this script only produces
    the fixture. The empirical observation of whether stitching activates
    happens at compile time and is recorded in the baseline JSON.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "stitched_table_across_pages.pdf"
    doc = pymupdf.open()

    header = ["Region",  "Units",  "Revenue"]
    page_rows = [
        [["North",   "1200",   "48000"], ["South",   "980",    "39200"], ["East", "1420", "56800"]],
        [["Central", "1310",   "52400"], ["Mountain","1050",   "42000"], ["Coast", "1490", "59600"]],
        [["Prairie", "1180",   "47200"], ["Islands", "620",    "24800"], ["Delta", "1330", "53200"]],
    ]
    for i, rows in enumerate(page_rows, start=1):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Regional Sales Table (page {i} of {len(page_rows)})",
                         fontsize=13, fontname="helv")
        page.insert_text((72, 100),
                         "Continued from the previous page." if i > 1 else "Starts here.",
                         fontsize=10)
        # Same header repeated on each page.
        _draw_grid_table(page, 72, 130, [header] + rows)

    doc.save(out)
    doc.close()
    return out


def generate_fixtures(out_dir: Path) -> FixturePaths:
    """Generate both fixture PDFs into ``out_dir`` and return their paths.

    Idempotent — regenerates on every call. Deterministic — PyMuPDF
    drawing has no randomness, so identical inputs produce byte-identical
    outputs.
    """
    return FixturePaths(
        multi_table_page=make_multi_table_page(out_dir),
        stitched_table_across_pages=make_stitched_cross_page_table(out_dir),
    )


def main() -> None:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="output directory (default: a fresh tempfile.mkdtemp() location)")
    args = ap.parse_args()

    out_dir = args.out_dir if args.out_dir is not None else Path(tempfile.mkdtemp(prefix="sp_fixtures_"))
    paths = generate_fixtures(out_dir)
    print(f"wrote {paths.multi_table_page}")
    print(f"wrote {paths.stitched_table_across_pages}")


if __name__ == "__main__":
    main()
