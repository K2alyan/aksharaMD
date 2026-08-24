"""Task 3 evidence: verify the stated block-level vs span-level mechanism
for simple2 and 4c against actual pipeline output.

For each doc:
  * Compile via CLI, dump per-page multicolumn_diagnostics (what the
    validator computed) and per-block x0/y0 positions.
  * Show whether blocks are in column-first order (low transition rate,
    one large y-drop) — which is what PR #56 was supposed to produce for 4c.
  * Show whether reading-order breakage would be a within-block (span-level)
    issue vs a between-block issue.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

DATA = Path(r"C:\Users\kalya\parsebench\data\docs")

TARGETS = [
    "text_multicolumns__simple2",
    "text_multicolumns__4c",
]


def compile_one(doc_id: str) -> dict | None:
    pdf = DATA / "text" / f"{doc_id}.pdf"
    if not pdf.exists():
        return None
    tmp = tempfile.mkdtemp(prefix="t3_")
    subprocess.run(
        ["aksharamd", "compile", str(pdf), "-o", tmp, "--quiet"],
        capture_output=True, text=True,
    )
    dj = list(Path(tmp).glob("*/document.json"))
    if not dj:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    d = json.load(open(dj[0], encoding="utf-8"))
    manifest = json.load(open(dj[0].parent / "manifest.json", encoding="utf-8"))
    shutil.rmtree(tmp, ignore_errors=True)
    return {"doc": d, "manifest": manifest}


for doc_id in TARGETS:
    r = compile_one(doc_id)
    if r is None:
        print(f"{doc_id}: NOT FOUND"); continue
    d = r["doc"]
    m = r["manifest"]
    meta = d.get("metadata", {})
    blocks = d.get("blocks", [])
    print(f"\n{'='*70}\n{doc_id}\n{'='*70}")
    print(f"  classification={meta.get('pdf_classification')} "
          f"rs={m.get('readiness_score')} band={m.get('quality_band')}")
    print(f"  warnings={m.get('warning_codes')}")

    # pdf_column_info
    col_info = meta.get("pdf_column_info", {})
    for pk, ci in col_info.items():
        print(f"  page {pk} column_info: boundaries={ci.get('boundaries')} "
              f"num_columns={ci.get('num_columns')} page_width={ci.get('page_width'):.1f}")

    # multicolumn_diagnostics (what the validator saw at block level)
    mc_diag = meta.get("multicolumn_diagnostics", {})
    print(f"  multicolumn_diagnostics.warned={mc_diag.get('warned')}")
    print(f"  problem_pages={mc_diag.get('problem_pages')}")
    for pa in mc_diag.get("page_analyses", []):
        print(f"    page {pa.get('page')}: "
              f"gap_rel={pa.get('gap_rel')} "
              f"transition_rate={pa.get('transition_rate')} "
              f"large_y_drops={pa.get('large_y_drops')} "
              f"short_frac={pa.get('short_frac')} "
              f"signals={pa.get('signals')} "
              f"warn={pa.get('warn')}")

    # Per-block positional dump (first 20 blocks)
    print(f"\n  BLOCKS (first 20 of {len(blocks)}):")
    print(f"    {'#':>3} {'page':>4} {'type':<10} {'x0':>7} {'y0':>7} {'content_head':<50}")
    positional = [b for b in blocks if b.get("metadata", {}).get("x0") is not None][:20]
    for i, b in enumerate(positional):
        m_b = b.get("metadata", {})
        head = (b.get("content") or "")[:50].replace("\n", " ")
        print(f"    {i:>3} {b.get('page') or '-':>4} {str(b.get('type'))[:10]:<10} "
              f"{m_b.get('x0', 0):>7.1f} {m_b.get('y0', 0):>7.1f} {head!r}")

    # Independent cluster analysis: if blocks are truly column-first, we
    # expect all left-cluster blocks then all right-cluster blocks.
    x0s = [b.get("metadata", {}).get("x0") for b in blocks
           if b.get("metadata", {}).get("x0") is not None]
    if x0s and len(x0s) >= 6:
        xs = sorted(set(round(x, 1) for x in x0s))
        gap, mid = 0.0, 0.0
        for j in range(1, len(xs)):
            g = xs[j] - xs[j-1]
            rel = (((xs[j] + xs[j-1]) / 2) - xs[0]) / (xs[-1] - xs[0]) if xs[-1] > xs[0] else 0
            if g > gap and 0.20 < rel < 0.80:
                gap = g; mid = (xs[j] + xs[j-1]) / 2
        clusters = [0 if b.get("metadata", {}).get("x0", 0) < mid else 1 for b in blocks
                    if b.get("metadata", {}).get("x0") is not None]
        # Longest run of same cluster
        max_run = cur = 1
        for k in range(1, len(clusters)):
            if clusters[k] == clusters[k-1]: cur += 1; max_run = max(max_run, cur)
            else: cur = 1
        transitions = sum(1 for k in range(1, len(clusters)) if clusters[k] != clusters[k-1])
        print(f"\n  INDEPENDENT cluster check: gap={gap:.1f} mid={mid:.1f}")
        print(f"    cluster sequence (first 40): {clusters[:40]}")
        print(f"    total blocks classified: {len(clusters)}  "
              f"left={clusters.count(0)}  right={clusters.count(1)}")
        print(f"    transitions={transitions}  longest_same_run={max_run}")
        print(f"    reading-order verdict: "
              f"{'COLUMN-FIRST (long runs of same cluster)' if max_run >= max(4, len(clusters)/3) else 'INTERLEAVED (short runs)'}")
