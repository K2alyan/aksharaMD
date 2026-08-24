"""Baseline capture for the SourceProfile refactor's byte-identity gate.

Compiles every dev-split doc via the ``aksharamd`` CLI and records the exact
outputs the byte-identity gate must preserve:

  * readiness_score              (int)
  * quality_band                 (HIGH | OK | RISKY | POOR)
  * warning_codes                (sorted list of strings)
  * notes                        (list of strings, order preserved)

quality_band is sourced from ``manifest.json`` (the pipeline's actual write
site), not re-derived from ``_quality_band(score)``. The two are equivalent
by construction (``_quality_band`` at ``aksharamd/models/manifest.py:16-23``
is a pure function of ``score``), but sourcing from the manifest captures
what actually shipped.

Also records ``pages_containing_tables`` (from ``pdf_stats["table_pages"]``)
and the block-count derivation ``len({b.page for b in blocks if b.type ==
TABLE and b.page is not None})``. When the two disagree, that doc exercises
the risky field the SourceProfile refactor must preserve — either a
multi-table page or a stitched cross-page table.

Usage:
    python tools/analysis/sourceprofile_baseline.py [--out <path>]

Writes to tools/analysis/sourceprofile_baseline_<git-tip-sha>.json by
default so pre- and post-refactor runs never collide.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CORPUS = Path(r"C:\Users\kalya\parsebench\benchmarks\calibration_corpus.jsonl")
DATA = Path(r"C:\Users\kalya\parsebench\data\docs")

# Fixtures added because the parsebench dev split is a single-page-per-doc
# corpus and structurally cannot exercise the pages_containing_tables
# divergence the design's Section 2.3 flags (multi-table pages, stitched
# cross-page tables). Fixture PDFs are NEVER checked into the repo
# (per the no-PDF-in-git policy at tests/test_parsebench_page_ground_truth.py);
# they are regenerated into a tmp directory each baseline run by
# generate_sourceprofile_fixtures.generate_fixtures(). PyMuPDF drawing is
# deterministic, so the bytes are stable across runs.
#
# Gate integrity note: byte-identity of the baseline JSON assumes PyMuPDF
# produces byte-identical fixture PDFs across the pre- and post-refactor
# runs. If a PyMuPDF version bump changes emitted PDF bytes, the fixture
# rows of the gate diff may show drift that looks like a scoring regression
# but is actually a fixture drift. If the gate fails only on the fixture
# rows, verify PyMuPDF hasn't upgraded before concluding the refactor
# broke something.
FIXTURE_SPEC = [
    ("_fixture__multi_table_page",           "multi_table_page",           "fixture_multi_table_page"),
    ("_fixture__stitched_table_across_pages", "stitched_table_across_pages", "fixture_stitched_cross_page"),
]


def _subdir_for_tier(tier: str) -> str:
    return "table" if tier == "table_heavy" else "text"


def _current_sha() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _dev_docs() -> list[dict]:
    return [
        json.loads(l)
        for l in open(CORPUS, encoding="utf-8")
        if l.strip()
        for r in [json.loads(l)]
        if r.get("split") == "dev"
    ]


def _dev_docs_clean() -> list[dict]:
    rows = []
    with open(CORPUS, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("split") == "dev":
                rows.append(r)
    return rows


def _compile_pdf(pdf: Path) -> dict:
    if not pdf.exists():
        return {"error": f"pdf not found: {pdf}"}
    tmp = tempfile.mkdtemp(prefix="baseline_")
    try:
        proc = subprocess.run(
            ["aksharamd", "compile", str(pdf), "-o", tmp, "--quiet"],
            capture_output=True, text=True,
        )
        docjs = list(Path(tmp).glob("*/document.json"))
        manjs = list(Path(tmp).glob("*/manifest.json"))
        if not docjs or not manjs:
            return {
                "error": "compile did not produce document.json/manifest.json",
                "stdout": proc.stdout[-500:],
                "stderr": proc.stderr[-500:],
            }
        d = json.load(open(docjs[0], encoding="utf-8"))
        m = json.load(open(manjs[0], encoding="utf-8"))
        # Block-count derivation of pages_containing_tables — the fidelity
        # fallback the design's Section 2.3 flags as lossy vs the true
        # pdf_stats["table_pages"] value. We record BOTH so the snapshot-
        # coverage check can identify docs that exercise the divergence.
        pages_with_table_blocks = len({
            b.get("page")
            for b in d.get("blocks", [])
            if b.get("type") == "table" and b.get("page") is not None
        })
        meta = d.get("metadata", {})
        stats = meta.get("pdf_stats", {})
        pages_containing_tables = int(stats.get("table_pages", 0))
        return {
            "readiness_score": m.get("readiness_score"),
            "quality_band": m.get("quality_band"),
            "warning_codes": sorted(m.get("warning_codes", []) or []),
            "notes": list(m.get("confidence_notes", []) or []),
            "pages_containing_tables": pages_containing_tables,
            "pages_with_table_blocks_derived": pages_with_table_blocks,
            "table_count": len([b for b in d.get("blocks", []) if b.get("type") == "table"]),
            "page_count": stats.get("page_count"),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _compile_one(doc_id: str, tier: str) -> dict:
    subdir = _subdir_for_tier(tier)
    return _compile_pdf(DATA / subdir / f"{doc_id}.pdf")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None,
                    help="output path (default: tools/analysis/sourceprofile_baseline_<sha>.json)")
    args = ap.parse_args()

    sha = _current_sha()
    if args.out is None:
        out_path = Path(f"tools/analysis/sourceprofile_baseline_{sha}.json")
    else:
        out_path = Path(args.out)

    rows = _dev_docs_clean()
    print(f"[baseline] git tip: {sha}", file=sys.stderr)
    print(f"[baseline] dev docs: {len(rows)}", file=sys.stderr)
    print(f"[baseline] output: {out_path}", file=sys.stderr)

    results: dict[str, dict] = {"__meta__": {
        "git_sha": sha,
        "corpus": str(CORPUS),
        "data_root": str(DATA),
        "dev_doc_count": len(rows),
    }}

    total = len(rows) + len(FIXTURE_SPEC)
    for i, r in enumerate(rows, 1):
        doc_id = r["doc_id"]
        tier = r.get("tier", "unknown")
        print(f"[baseline] {i:>2}/{total} compiling {doc_id} ({tier})", file=sys.stderr)
        entry = _compile_one(doc_id, tier)
        entry["tier"] = tier
        results[doc_id] = entry

    # Regenerate fixture PDFs into a fresh tmp dir; never persist them in
    # the repo (see FIXTURE_SPEC comment above).
    fixtures_tmp = Path(tempfile.mkdtemp(prefix="sp_baseline_fixtures_"))
    try:
        from generate_sourceprofile_fixtures import generate_fixtures
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from generate_sourceprofile_fixtures import generate_fixtures
    fixture_paths = generate_fixtures(fixtures_tmp)
    fixture_map = {
        "multi_table_page": fixture_paths.multi_table_page,
        "stitched_table_across_pages": fixture_paths.stitched_table_across_pages,
    }
    try:
        for j, (doc_id, fname, tier) in enumerate(FIXTURE_SPEC, start=len(rows) + 1):
            print(f"[baseline] {j:>2}/{total} compiling {doc_id} ({tier})", file=sys.stderr)
            entry = _compile_pdf(fixture_map[fname])
            entry["tier"] = tier
            results[doc_id] = entry
    finally:
        shutil.rmtree(fixtures_tmp, ignore_errors=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, sort_keys=False), encoding="utf-8")
    print(f"[baseline] wrote {out_path}", file=sys.stderr)

    # Snapshot-coverage summary (informational).
    print("", file=sys.stderr)
    print("[coverage] Docs where pages_containing_tables != pages_with_table_blocks_derived:", file=sys.stderr)
    print("[coverage] (these are the docs where the SourceProfile risky-field regression would show)", file=sys.stderr)
    any_divergent = False
    for doc_id, r in results.items():
        if doc_id == "__meta__":
            continue
        if "error" in r:
            continue
        pct = r.get("pages_containing_tables") or 0
        pbd = r.get("pages_with_table_blocks_derived") or 0
        tbc = r.get("table_count") or 0
        if pct != pbd:
            any_divergent = True
            print(f"[coverage]   {doc_id}: table_pages={pct} block_derived={pbd} table_blocks={tbc}", file=sys.stderr)
        elif tbc > pct and pct > 0:
            # Multi-table page: same page count, but more table blocks than pages
            any_divergent = True
            print(f"[coverage]   {doc_id}: table_pages={pct} block_derived={pbd} table_blocks={tbc} (MULTI-TABLE PAGE)", file=sys.stderr)
    if not any_divergent:
        print("[coverage]   NONE — snapshot set does not exercise multi-table/stitched divergence", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
