"""
AksharaMD vs LiteParse — head-to-head benchmark on py-pdf/sample-files corpus.

Metrics collected per PDF:
  - chars_extracted      : len(full text output)
  - tables_found         : Markdown table blocks in output
  - elapsed_s            : wall time in seconds
  - error                : exception message if parse failed, else ""
  AksharaMD only:
  - readiness_score      : 0-100
  - quality_band         : HIGH / OK / RISKY / POOR
  - warning_codes        : list of machine-readable codes

Output: prints a Rich table to stdout and writes benchmark_results_liteparse.json
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

console = Console(highlight=False)

PDF_DIR = Path("C:/Users/kalya/Downloads/pdf-samples")
TESSDATA = "C:/Program Files/Tesseract-OCR/tessdata"

# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

_TABLE_ROW_RE = re.compile(r"^\|.+\|", re.MULTILINE)

def _count_tables(text: str) -> int:
    """Count Markdown tables (separator rows like |---|) in a text string."""
    return len(re.findall(r"^\|[-| :]+\|", text, re.MULTILINE))


def _collect_pdfs() -> list[Path]:
    pdfs = sorted(PDF_DIR.rglob("*.pdf"))
    # Skip password-protected file — neither tool can open it without the password
    pdfs = [p for p in pdfs if "password" not in p.name.lower()]
    return pdfs


# --------------------------------------------------------------------------- #
# runners                                                                      #
# --------------------------------------------------------------------------- #

_TIMEOUT = 45  # seconds per file per tool


def _run_with_timeout(fn, pdf):
    """Run fn(pdf) in a thread; return error dict if it exceeds _TIMEOUT seconds."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(fn, pdf)
        try:
            return future.result(timeout=_TIMEOUT)
        except concurrent.futures.TimeoutError:
            return {"chars": 0, "tables": 0, "elapsed_s": _TIMEOUT,
                    "score": 0, "band": "POOR", "warnings": [], "error": f"TIMEOUT >{_TIMEOUT}s"}
        except Exception as exc:
            return {"chars": 0, "tables": 0, "elapsed_s": 0,
                    "score": 0, "band": "POOR", "warnings": [], "error": str(exc)[:80]}


def _aksharamd_core(pdf: Path) -> dict:
    # Patch out Marker for benchmark — compares base extraction only (no GPU)
    import aksharamd.plugins.parsers.pdf as _pdf_mod
    _pdf_mod._MARKER_AVAILABLE = False
    from aksharamd.compiler import Compiler
    t0 = time.perf_counter()
    ctx = Compiler(output_dir=str(Path("output/_bench") / pdf.stem)).compile(str(pdf))
    elapsed = round(time.perf_counter() - t0, 2)
    m = ctx.manifest
    if m is None:
        return {"chars": 0, "tables": 0, "elapsed_s": elapsed,
                "score": 0, "band": "POOR", "warnings": ["no manifest"],
                "error": "no manifest produced"}
    text = " ".join(b.content for b in (ctx.document.blocks if ctx.document else []))
    return {
        "chars": len(text),
        "tables": m.tables,
        "elapsed_s": elapsed,
        "score": m.readiness_score,
        "band": m.quality_band,
        "warnings": m.warning_codes,
        "error": "; ".join(m.errors) if m.errors else "",
    }


def run_aksharamd(pdf: Path) -> dict:
    return _run_with_timeout(_aksharamd_core, pdf)


def _liteparse_core(pdf: Path) -> dict:
    from liteparse.parser import LiteParse
    parser = LiteParse(
        output_format="markdown",
        ocr_enabled=True,
        tessdata_path=TESSDATA,
        quiet=True,
        ocr_failure_fatal=False,
    )
    t0 = time.perf_counter()
    result = parser.parse(str(pdf))
    elapsed = round(time.perf_counter() - t0, 2)
    text = result.text or ""
    return {
        "chars": len(text),
        "tables": _count_tables(text),
        "elapsed_s": elapsed,
        "error": "",
    }


def run_liteparse(pdf: Path) -> dict:
    return _run_with_timeout(_liteparse_core, pdf)


# --------------------------------------------------------------------------- #
# main                                                                         #
# --------------------------------------------------------------------------- #

def main():
    pdfs = _collect_pdfs()
    console.print(f"[bold]Benchmarking {len(pdfs)} PDFs — AksharaMD vs LiteParse[/]\n")

    results = []

    for pdf in pdfs:
        label = f"{pdf.parent.name}/{pdf.name}"
        console.print(f"  [dim]{label}[/]", end="")

        a = run_aksharamd(pdf)
        l = run_liteparse(pdf)

        results.append({"pdf": label, "aksharamd": a, "liteparse": l})
        console.print(f"  aksharamd={a['score']}/100  liteparse_chars={l['chars']:,}")

    # ── Summary table ─────────────────────────────────────────────────────── #
    console.print()
    t = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    t.add_column("PDF", min_width=35)
    t.add_column("A.MD score", justify="right")
    t.add_column("A.MD chars", justify="right")
    t.add_column("A.MD tabs", justify="right")
    t.add_column("A.MD time", justify="right")
    t.add_column("LP chars", justify="right")
    t.add_column("LP tabs", justify="right")
    t.add_column("LP time", justify="right")
    t.add_column("Winner")

    amd_wins = lp_wins = ties = 0

    for r in results:
        a = r["aksharamd"]
        l = r["liteparse"]

        score = a["score"]
        band = a["band"]
        if band == "HIGH":
            score_str = f"[green]{score}[/]"
        elif band == "OK":
            score_str = f"[yellow]{score}[/]"
        else:
            score_str = f"[red]{score}[/]"

        a_ok = not a["error"] and a["chars"] > 0
        l_ok = not l["error"] and l["chars"] > 0

        # Winner: primarily by chars extracted (proxy for coverage)
        # Ties when within 5% of each other on chars and both succeeded
        if a_ok and l_ok:
            ratio = a["chars"] / max(l["chars"], 1)
            if ratio > 1.05:
                winner = "[green]AksharaMD[/]"
                amd_wins += 1
            elif ratio < 0.95:
                winner = "[yellow]LiteParse[/]"
                lp_wins += 1
            else:
                winner = "[dim]tie[/]"
                ties += 1
        elif a_ok and not l_ok:
            winner = "[green]AksharaMD[/]"
            amd_wins += 1
        elif l_ok and not a_ok:
            winner = "[yellow]LiteParse[/]"
            lp_wins += 1
        else:
            winner = "[dim]both failed[/]"

        a_tabs = str(a["tables"]) if a_ok else "[red]err[/]"
        l_tabs = str(l["tables"]) if l_ok else "[red]err[/]"
        a_chars = f"{a['chars']:,}" if a_ok else "[red]err[/]"
        l_chars = f"{l['chars']:,}" if l_ok else "[red]err[/]"

        t.add_row(
            r["pdf"],
            score_str,
            a_chars,
            a_tabs,
            f"{a['elapsed_s']}s",
            l_chars,
            l_tabs,
            f"{l['elapsed_s']}s",
            winner,
        )

    console.print(t)

    total = len(results)
    console.print(f"\n[bold]Summary:[/] {total} PDFs")
    console.print(f"  [green]AksharaMD[/] extracted more chars: {amd_wins}")
    console.print(f"  [yellow]LiteParse[/] extracted more chars: {lp_wins}")
    console.print(f"  [dim]Ties (within 5%):[/]               {ties}")

    # Aggregate stats
    a_scores = [r["aksharamd"]["score"] for r in results if not r["aksharamd"]["error"]]
    a_times  = [r["aksharamd"]["elapsed_s"] for r in results]
    l_times  = [r["liteparse"]["elapsed_s"] for r in results]
    a_tables = sum(r["aksharamd"]["tables"] for r in results)
    l_tables = sum(r["liteparse"]["tables"] for r in results)
    a_errors = sum(1 for r in results if r["aksharamd"]["error"])
    l_errors = sum(1 for r in results if r["liteparse"]["error"])

    console.print(f"\n  AksharaMD — avg score: {round(sum(a_scores)/len(a_scores), 1) if a_scores else 'n/a'}  "
                  f"avg time: {round(sum(a_times)/len(a_times), 2)}s  "
                  f"total tables: {a_tables}  errors: {a_errors}")
    console.print(f"  LiteParse  — avg time: {round(sum(l_times)/len(l_times), 2)}s  "
                  f"total tables: {l_tables}  errors: {l_errors}")

    # Save raw results
    out = Path("benchmark_results_liteparse.json")
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"\n[dim]Raw results written to {out}[/]")


if __name__ == "__main__":
    main()
