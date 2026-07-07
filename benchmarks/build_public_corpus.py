#!/usr/bin/env python3
"""Build the public reproducible benchmark corpus.

Downloads the py-pdf/sample-files PDF subset and generates all synthetic
format fixtures into benchmarks/.public_corpus/.

Usage:
    python benchmarks/build_public_corpus.py [--dry-run] [--skip-pdf]

The corpus directory is gitignored; run this script once before
running run_public_benchmark.py.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BENCHMARKS = Path(__file__).parent
MANIFEST_PATH = BENCHMARKS / "public_corpus_manifest.json"
CORPUS_ROOT = BENCHMARKS / ".public_corpus"

_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2.0


def _load_manifest() -> dict:
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _download(url: str, dest: Path, dry_run: bool = False) -> bool:
    """Download url to dest. Returns True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  skip (cached): {dest.name}")
        return True
    if dry_run:
        print(f"  [dry-run] would download: {url}")
        return True
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "aksharamd-benchmark/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                dest.write_bytes(resp.read())
            print(f"  downloaded ({dest.stat().st_size:,} B): {dest.name}")
            return True
        except urllib.error.HTTPError as exc:
            print(f"  HTTP {exc.code} on attempt {attempt}/{_RETRY_ATTEMPTS}: {url}")
            if exc.code in (403, 404):
                return False
        except Exception as exc:
            print(f"  error on attempt {attempt}/{_RETRY_ATTEMPTS}: {exc}")
        if attempt < _RETRY_ATTEMPTS:
            time.sleep(_RETRY_DELAY)
    return False


def _build_docx(dest: Path) -> None:
    try:
        from docx import Document
        doc = Document()
        doc.add_heading("AksharaMD Benchmark Document", 0)
        doc.add_paragraph(
            "This is a synthetic DOCX fixture for the AksharaMD public benchmark. "
            "It contains headings, paragraphs, and a table."
        )
        doc.add_heading("Section 1: Introduction", level=1)
        doc.add_paragraph(
            "The quick brown fox jumps over the lazy dog. "
            "Pack my box with five dozen liquor jugs."
        )
        doc.add_heading("Section 2: Data Table", level=1)
        table = doc.add_table(rows=3, cols=3)
        table.cell(0, 0).text = "Name"
        table.cell(0, 1).text = "Value"
        table.cell(0, 2).text = "Unit"
        table.cell(1, 0).text = "Precision"
        table.cell(1, 1).text = "0.95"
        table.cell(1, 2).text = "ratio"
        table.cell(2, 0).text = "Recall"
        table.cell(2, 1).text = "0.88"
        table.cell(2, 2).text = "ratio"
        doc.save(str(dest))
    except ImportError:
        dest.write_text(
            "[DOCX skipped: python-docx not installed. "
            "pip install python-docx to generate this fixture.]\n"
        )


def _build_xlsx(dest: Path) -> None:
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Benchmark"
        ws.append(["Document", "Format", "Pages", "Blocks", "Score"])
        rows = [
            ("sample-001", "PDF", 4, 12, 0.91),
            ("sample-002", "DOCX", 2, 8, 0.87),
            ("sample-003", "HTML", 1, 6, 0.95),
            ("sample-004", "TXT", 1, 3, 0.78),
        ]
        for row in rows:
            ws.append(list(row))
        ws2 = wb.create_sheet("Summary")
        ws2.append(["Metric", "Value"])
        ws2.append(["Total Files", 4])
        ws2.append(["Success Rate", "100%"])
        ws2.append(["Mean Score", 0.8775])
        wb.save(str(dest))
    except ImportError:
        dest.write_text(
            "[XLSX skipped: openpyxl not installed. "
            "pip install openpyxl to generate this fixture.]\n"
        )


def _build_pptx(dest: Path) -> None:
    try:
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        slide1 = prs.slides.add_slide(prs.slide_layouts[0])
        slide1.shapes.title.text = "AksharaMD Benchmark Deck"
        slide1.placeholders[1].text = "Public Reproducible Corpus — Synthetic PPTX Fixture"
        slide2 = prs.slides.add_slide(prs.slide_layouts[1])
        slide2.shapes.title.text = "Supported Formats"
        tf = slide2.placeholders[1].text_frame
        tf.text = "PDF (25 real files)"
        tf.add_paragraph().text = "DOCX, XLSX, PPTX (synthetic)"
        tf.add_paragraph().text = "HTML, CSV, JSON, XML, TXT, MD, ZIP (synthetic)"
        slide3 = prs.slides.add_slide(prs.slide_layouts[1])
        slide3.shapes.title.text = "Metrics Recorded"
        tf3 = slide3.placeholders[1].text_frame
        tf3.text = "Parser success/failure"
        tf3.add_paragraph().text = "Block count and types"
        tf3.add_paragraph().text = "Output character count"
        tf3.add_paragraph().text = "Estimated token count"
        _ = Inches  # used for import completeness
        prs.save(str(dest))
    except ImportError:
        dest.write_text(
            "[PPTX skipped: python-pptx not installed. "
            "pip install python-pptx to generate this fixture.]\n"
        )


def _build_html(dest: Path) -> None:
    dest.write_text(
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>AksharaMD Benchmark Page</title>
</head>
<body>
  <h1>AksharaMD Benchmark: HTML Fixture</h1>
  <p>This page is a synthetic HTML fixture for the AksharaMD public benchmark suite.</p>
  <h2>Feature Table</h2>
  <table>
    <thead><tr><th>Feature</th><th>Status</th></tr></thead>
    <tbody>
      <tr><td>Table extraction</td><td>Supported</td></tr>
      <tr><td>Heading detection</td><td>Supported</td></tr>
      <tr><td>Link preservation</td><td>Supported</td></tr>
      <tr><td>Image alt text</td><td>Supported</td></tr>
    </tbody>
  </table>
  <h2>Sample Paragraph</h2>
  <p>The quick brown fox jumps over the lazy dog.
     Pack my box with five dozen liquor jugs.</p>
  <ul>
    <li>Item one</li>
    <li>Item two</li>
    <li>Item three</li>
  </ul>
</body>
</html>
""",
        encoding="utf-8",
    )


def _build_csv(dest: Path) -> None:
    dest.write_text(
        "id,format,pages,blocks,score,notes\n"
        "1,pdf,4,12,0.91,multipage pdflatex\n"
        "2,docx,2,8,0.87,synthetic fixture\n"
        "3,html,1,6,0.95,simple page\n"
        "4,txt,1,3,0.78,plain text\n"
        "5,xlsx,3,15,0.82,spreadsheet with two sheets\n"
        "6,pptx,3,9,0.79,three-slide deck\n",
        encoding="utf-8",
    )


def _build_json(dest: Path) -> None:
    data = {
        "benchmark": "aksharamd-public-v1",
        "description": "Synthetic JSON fixture for parser coverage testing.",
        "formats_tested": ["pdf", "docx", "xlsx", "pptx", "html", "csv", "json", "xml", "txt", "md", "zip"],
        "results": [
            {"id": f"file-{i:03d}", "format": fmt, "success": True, "blocks": n}
            for i, (fmt, n) in enumerate([
                ("pdf", 12), ("docx", 8), ("html", 6), ("txt", 3),
                ("csv", 4), ("json", 2), ("xml", 5), ("md", 7),
            ], start=1)
        ],
        "summary": {
            "total": 8,
            "success": 8,
            "failure": 0,
            "success_rate": 1.0,
        },
    }
    dest.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _build_xml(dest: Path) -> None:
    dest.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<benchmark version="1.0">
  <description>Synthetic XML fixture for AksharaMD parser coverage benchmarks.</description>
  <corpus>
    <file id="pdf-001" format="pdf" pages="1">
      <label>minimal-pdflatex</label>
      <expected_outcome>success</expected_outcome>
    </file>
    <file id="syn-001" format="docx" pages="2">
      <label>synthetic-docx</label>
      <expected_outcome>success</expected_outcome>
    </file>
    <file id="syn-004" format="html" pages="1">
      <label>synthetic-html</label>
      <expected_outcome>success</expected_outcome>
    </file>
  </corpus>
  <metrics>
    <metric name="success_rate" value="1.0" unit="ratio"/>
    <metric name="mean_blocks" value="7.5" unit="count"/>
  </metrics>
</benchmark>
""",
        encoding="utf-8",
    )


def _build_txt(dest: Path) -> None:
    dest.write_text(
        "AksharaMD Public Benchmark — Plain Text Fixture\n"
        "================================================\n\n"
        "This file is a plain-text fixture for testing the AksharaMD text parser.\n\n"
        "Section 1: Background\n"
        "---------------------\n"
        "AksharaMD is an open-source document ingestion pipeline that converts 40+\n"
        "file formats into structured Markdown and JSON suitable for RAG workflows.\n\n"
        "Section 2: Supported Formats\n"
        "----------------------------\n"
        "PDF, DOCX, XLSX, PPTX, HTML, EPUB, CSV, JSON, XML, TXT, Markdown,\n"
        "ZIP, TAR, 7z, EML, and more.\n\n"
        "Section 3: Benchmark Scope\n"
        "--------------------------\n"
        "This benchmark measures parser coverage and extraction readiness.\n"
        "It does not measure answer correctness or RAG faithfulness.\n",
        encoding="utf-8",
    )


def _build_md(dest: Path) -> None:
    dest.write_text(
        "# AksharaMD Public Benchmark\n\n"
        "Synthetic Markdown fixture for parser coverage testing.\n\n"
        "## Overview\n\n"
        "This corpus tests AksharaMD's ability to extract structured content from\n"
        "a diverse set of document formats.\n\n"
        "## Format Coverage\n\n"
        "| Format | Source | Count |\n"
        "| --- | --- | --- |\n"
        "| PDF | py-pdf/sample-files (CC-BY-SA-4.0) | 25 |\n"
        "| DOCX | synthetic | 1 |\n"
        "| XLSX | synthetic | 1 |\n"
        "| PPTX | synthetic | 1 |\n"
        "| HTML | synthetic | 1 |\n"
        "| CSV | synthetic | 1 |\n"
        "| JSON | synthetic | 1 |\n"
        "| XML | synthetic | 1 |\n"
        "| TXT | synthetic | 1 |\n"
        "| MD | synthetic | 1 |\n"
        "| ZIP | synthetic | 1 |\n\n"
        "## What This Measures\n\n"
        "- Parser success/failure rate\n"
        "- Block structure (headings, paragraphs, tables, code blocks)\n"
        "- Output character count\n"
        "- Estimated token count\n\n"
        "## What This Does Not Measure\n\n"
        "- Answer correctness\n"
        "- RAG faithfulness\n"
        "- Citation accuracy\n"
        "- Semantic agent performance\n",
        encoding="utf-8",
    )


def _build_zip(dest: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.md", "# Mixed Archive\n\nThis ZIP is a synthetic benchmark fixture.\n")
        zf.writestr("data/config.json", '{"version": "1.0", "format": "zip-fixture"}\n')
        zf.writestr("data/notes.txt", "Plain text note inside a ZIP archive.\n")
        zf.writestr(
            "src/hello.py",
            'def greet(name: str) -> str:\n    """Return a greeting."""\n    return f"Hello, {name}!"\n',
        )
        zf.writestr(
            "data/table.csv",
            "col_a,col_b,col_c\n1,foo,true\n2,bar,false\n3,baz,true\n",
        )
    dest.write_bytes(buf.getvalue())


_SYNTHETIC_BUILDERS: dict[str, object] = {  # values are Callable[[Path], None]
    "docx": _build_docx,
    "xlsx": _build_xlsx,
    "pptx": _build_pptx,
    "html": _build_html,
    "csv": _build_csv,
    "json": _build_json,
    "xml": _build_xml,
    "txt": _build_txt,
    "md": _build_md,
    "zip": _build_zip,
}


def build(dry_run: bool = False, skip_pdf: bool = False) -> dict[str, int]:
    manifest = _load_manifest()
    corpus_root = BENCHMARKS / manifest["corpus_dir"]
    corpus_root.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {"downloaded": 0, "generated": 0, "skipped": 0, "failed": 0}

    for entry in manifest["files"]:
        dest = corpus_root / entry["local_path"]
        fmt = entry["format"]
        source = entry["source"]

        print(f"[{entry['id']}] {entry['label']} ({fmt})")

        if source == "py-pdf/sample-files":
            if skip_pdf:
                print("  skip (--skip-pdf)")
                counts["skipped"] += 1
                continue
            ok = _download(entry["url"], dest, dry_run=dry_run)
            if ok:
                counts["downloaded"] += 1
            else:
                counts["failed"] += 1

        elif source == "synthetic":
            if dest.exists():
                print(f"  skip (cached): {dest.name}")
                counts["skipped"] += 1
                continue
            if dry_run:
                print(f"  [dry-run] would generate: {dest.name}")
                counts["generated"] += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            builder = _SYNTHETIC_BUILDERS.get(fmt)
            if builder is None:
                print(f"  warning: no builder for format '{fmt}' — skipping")
                counts["skipped"] += 1
                continue
            try:
                builder(dest)  # type: ignore[operator]
                print(f"  generated ({dest.stat().st_size:,} B): {dest.name}")
                counts["generated"] += 1
            except Exception as exc:
                print(f"  error generating {dest.name}: {exc}")
                counts["failed"] += 1

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without writing files")
    parser.add_argument("--skip-pdf", action="store_true", help="Skip downloading PDF files (synthetic formats only)")
    args = parser.parse_args()

    print(f"Building corpus into: {BENCHMARKS / '.public_corpus'}\n")
    counts = build(dry_run=args.dry_run, skip_pdf=args.skip_pdf)

    print(
        f"\nDone. downloaded={counts['downloaded']}  "
        f"generated={counts['generated']}  "
        f"skipped={counts['skipped']}  "
        f"failed={counts['failed']}"
    )
    if counts["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
