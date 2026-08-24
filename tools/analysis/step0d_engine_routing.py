"""Task: enumerate engine attribution for all 25 dev-split docs.

Routing summary from code (pdf.py):
  * Phase 1 (_extract_raw_page): PyMuPDF text extraction on every page. Also
    computes per-page ocr_pixmap when the page has < _OCR_TEXT_THRESHOLD chars.
  * Phase 3 (_process_raw_page): per-page Tesseract OCR runs when
    (a) ocr_pixmap is not None AND
    (b) use_alternate_ocr_backend is False (default) AND
    (c) _ocr_available() returns True (pytesseract installed).
  * Phase 5 (_apply_marker_to_image_pages): Marker runs on the image-only
    pages when (a) _marker_available() is True AND (b) use_marker_phase is True
    (i.e. NOT `--ocr-backend unlimited_ocr`) AND (c) at least one page has
    ocr_pixmap != None.

`pdf_vision_pages` in metadata records the count of pages Marker actually
processed. `pdf_ocr_available` records whether Tesseract was available.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

corpus = [
    json.loads(l) for l in open(
        r"C:\Users\kalya\parsebench\benchmarks\calibration_corpus.jsonl",
        encoding="utf-8",
    ) if l.strip()
]
dev = [r for r in corpus if r.get("split") == "dev"]

DATA = Path(r"C:\Users\kalya\parsebench\data\docs")


def compile_doc(doc_id: str, tier: str) -> dict | None:
    subdir = "table" if tier == "table_heavy" else "text"
    pdf = DATA / subdir / f"{doc_id}.pdf"
    if not pdf.exists():
        return None
    tmp = tempfile.mkdtemp(prefix="s0d_")
    subprocess.run(
        ["aksharamd", "compile", str(pdf), "-o", tmp, "--quiet"],
        capture_output=True, text=True,
    )
    dj = list(Path(tmp).glob("*/document.json"))
    if not dj:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    d = json.load(open(dj[0], encoding="utf-8"))
    shutil.rmtree(tmp, ignore_errors=True)
    meta = d.get("metadata", {})
    stats = meta.get("pdf_stats", {})
    return {
        "classification": meta.get("pdf_classification"),
        "page_count": stats.get("page_count"),
        "image_pages": stats.get("image_pages"),
        "text_pages": stats.get("text_pages"),
        "table_pages": stats.get("table_pages"),
        "pdf_ocr_available": meta.get("pdf_ocr_available"),
        "pdf_vision_pages": meta.get("pdf_vision_pages"),
    }


# Also check env-level engine availability
def env_info() -> dict:
    info = {}
    try:
        import marker  # noqa
        info["marker_installed"] = True
        info["marker_version"] = getattr(marker, "__version__", "?")
    except Exception:
        info["marker_installed"] = False
    try:
        import pytesseract  # noqa
        info["pytesseract_installed"] = True
    except Exception:
        info["pytesseract_installed"] = False
    return info


print("=== Environment ===")
print(json.dumps(env_info(), indent=2))
print()

rows = []
for r in dev:
    a = compile_doc(r["doc_id"], r.get("tier"))
    if a is None:
        rows.append({"doc_id": r["doc_id"], "err": "not-found"}); continue
    rows.append({"doc_id": r["doc_id"], "tier": r.get("tier"), **a})

# Print table
print(f"{'doc_id':<45} {'tier':<15} {'cls':<14} {'pages':>5} {'img':>4} "
      f"{'vision':>6} {'ocr_avail':>9}")
print("-" * 120)
for r in rows:
    if "err" in r:
        print(f"{r['doc_id']:<45} MISSING"); continue
    print(f"{r['doc_id']:<45} {r.get('tier','?'):<15} {str(r['classification']):<14} "
          f"{r['page_count']!s:>5} {r['image_pages']!s:>4} "
          f"{r['pdf_vision_pages']!s:>6} {r['pdf_ocr_available']!s:>9}")

# Summaries
print()
default_only = [r for r in rows if "err" not in r and (r["image_pages"] or 0) == 0]
image_pages = [r for r in rows if "err" not in r and (r["image_pages"] or 0) > 0]
marker_fired = [r for r in rows if "err" not in r and (r["pdf_vision_pages"] or 0) > 0]

print(f"Default-only (no OCR, no Marker):  {len(default_only)} docs")
print(f"Docs with at least one image page: {len(image_pages)}")
print(f"Docs where Marker actually ran:    {len(marker_fired)}")
print()
print("Image-page docs (candidates for Marker + OCR):")
for r in image_pages:
    print(f"  {r['doc_id']}  image_pages={r['image_pages']}  "
          f"pdf_vision_pages={r['pdf_vision_pages']}  ocr_avail={r['pdf_ocr_available']}")

# Focus attribution for de, 4c, simple2
print()
print("=== Residual attribution ===")
focus = ["text_dense__de", "text_multicolumns__4c", "text_multicolumns__simple2"]
for f in focus:
    match = next((r for r in rows if r["doc_id"] == f), None)
    if match is None:
        print(f"  {f}: NOT COMPILED"); continue
    engine = "default-only (PyMuPDF/pdfplumber; no OCR, no Marker)"
    if (match["image_pages"] or 0) > 0:
        # Could be OCR + Marker
        parts = []
        if (match["pdf_vision_pages"] or 0) > 0:
            parts.append("Marker")
        if match["pdf_ocr_available"]:
            parts.append("Tesseract-eligible")
        engine = f"image-page path: {'+'.join(parts) if parts else 'no engine ran'}"
    print(f"  {f}: image_pages={match['image_pages']}, "
          f"vision_pages={match['pdf_vision_pages']} -> {engine}")
