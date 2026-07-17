"""Phase 5 table-expectation detector validation.

Runs the full parsebench table corpus (503 docs) plus negative-control
samples from text and chart categories through the current AksharaMD
pipeline, then measures precision/recall for W_TABLE_EXPECTED_NOT_EXTRACTED.

Ground truth is derived from the parsebench evaluation report:
  - "missed"   : tables_predicted=False  (no table produced at all)
  - "partial"  : tables_predicted=True but fewer tables than expected
  - "extracted": tables_predicted=True and found_actual >= found_expected
  - "negative" : docs from text/chart categories (no table expected)

Usage:
    cd C:/Users/kalya/omnimark
    python -m benchmarks.run_expectation_validation [--neg-controls N] [--out PATH]

Output:
    benchmarks/expectation_validation_run.json
"""
from __future__ import annotations

import json
import random
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import click

PARSEBENCH_ROOT = Path("C:/Users/kalya/parsebench")
EVAL_REPORT      = PARSEBENCH_ROOT / "output/aksharamd_parse/_evaluation_report.json"
DOCS_TABLE       = PARSEBENCH_ROOT / "data/docs/table"
DOCS_TEXT        = PARSEBENCH_ROOT / "data/docs/text"
DOCS_CHART       = PARSEBENCH_ROOT / "data/docs/chart"
DOCS_LAYOUT      = PARSEBENCH_ROOT / "data/docs/layout"
OMNIMARK_ROOT    = Path(__file__).parent.parent
DEFAULT_OUT      = OMNIMARK_ROOT / "benchmarks/expectation_validation_run.json"

SEED = 42

# ── Ground-truth loading ───────────────────────────────────────────────────────

@dataclass
class DocRecord:
    test_id: str
    pdf_path: str
    category: str            # "table", "text", "chart", "layout"
    gt_class: str            # "missed", "partial", "extracted", "negative"
    grits_con: float         # 0.0 if not applicable
    found_expected: int      # from eval report
    found_actual: int        # from eval report
    tags: list[str] = field(default_factory=list)


def _load_table_ground_truth() -> list[DocRecord]:
    """Load per-example table ground truth from the parsebench eval report."""
    with open(EVAL_REPORT, encoding="utf-8") as f:
        report = json.load(f)

    records: list[DocRecord] = []
    for e in report.get("per_example_results", []):
        if "table" not in e.get("tags", []):
            continue
        test_id = e["test_id"]
        pdf_rel = test_id.replace("table/", "", 1)
        pdf_path = DOCS_TABLE / f"{pdf_rel}.pdf"

        grits = 0.0
        found_expected = 0
        found_actual = 0
        tables_predicted = None

        for m in e.get("metrics", []):
            if m["metric_name"] == "grits_con":
                grits = float(m["value"] or 0.0)
                meta = m.get("metadata", {})
                tables_predicted = meta.get("tables_predicted")
                found_expected = int(meta.get("tables_found_expected") or 0)
                found_actual = int(meta.get("tables_found_actual") or 0)

        if tables_predicted is False and found_expected > 0:
            gt_class = "missed"
        elif tables_predicted is True and found_actual < found_expected:
            gt_class = "partial"
        elif tables_predicted is True:
            gt_class = "extracted"
        else:
            # tables_predicted=None or found_expected=0; treat as extracted/ambiguous
            gt_class = "extracted"

        records.append(DocRecord(
            test_id=test_id,
            pdf_path=str(pdf_path),
            category="table",
            gt_class=gt_class,
            grits_con=grits,
            found_expected=found_expected,
            found_actual=found_actual,
            tags=list(e.get("tags", [])),
        ))

    return records


def _load_negative_controls(n: int, seed: int) -> list[DocRecord]:
    """Sample n negative-control docs from text and chart categories."""
    rng = random.Random(seed)
    records: list[DocRecord] = []

    # Split n evenly between text and chart; add layout as a harder negative
    per_cat = max(1, n // 3)

    for cat, docs_dir in [("text", DOCS_TEXT), ("chart", DOCS_CHART), ("layout", DOCS_LAYOUT)]:
        pdfs = sorted(docs_dir.glob("*.pdf"))
        sampled = rng.sample(pdfs, min(per_cat, len(pdfs)))
        for pdf in sampled:
            records.append(DocRecord(
                test_id=f"{cat}/{pdf.stem}",
                pdf_path=str(pdf),
                category=cat,
                gt_class="negative",
                grits_con=0.0,
                found_expected=0,
                found_actual=0,
                tags=[cat],
            ))

    return records


# ── Compilation + detection ────────────────────────────────────────────────────

@dataclass
class PageResult:
    page: int
    expected: str              # "true" | "false" | "unknown"
    confidence: float
    warned: bool               # W_TABLE_EXPECTED_NOT_EXTRACTED was emitted for this page
    risk_families: list[str]
    risk_signals: list[str]
    extracted_table_count: int
    rejected_candidate_count: int


@dataclass
class RunRecord:
    test_id: str
    pdf_path: str
    category: str
    gt_class: str
    grits_con: float
    found_expected: int
    found_actual: int
    tags: list[str]
    # Detection results
    warning_count: int          # total W_TABLE_EXPECTED_NOT_EXTRACTED warnings emitted
    pages_expected_true: int    # pages where expected="true"
    pages_expected_unknown: int
    total_pages: int
    page_results: list[dict]
    error: str | None = None


def _run_doc(record: DocRecord, timeout_secs: int = 25) -> RunRecord:
    """Compile one document with a wall-clock timeout.

    Scanned PDFs that trigger the full OCR/vision pipeline can take minutes.
    We cap each doc at timeout_secs; docs that exceed it are skipped with an
    error tag so they don't inflate false-negative counts.
    """
    import threading

    from aksharamd.compiler import Compiler

    base = RunRecord(
        test_id=record.test_id,
        pdf_path=record.pdf_path,
        category=record.category,
        gt_class=record.gt_class,
        grits_con=record.grits_con,
        found_expected=record.found_expected,
        found_actual=record.found_actual,
        tags=record.tags,
        warning_count=0,
        pages_expected_true=0,
        pages_expected_unknown=0,
        total_pages=0,
        page_results=[],
    )

    if not Path(record.pdf_path).exists():
        base.error = f"pdf not found: {record.pdf_path}"
        return base

    ctx_holder: list = [None]
    exc_holder: list = [None]

    def _compile():
        try:
            compiler = Compiler()
            ctx_holder[0] = compiler.compile(record.pdf_path)
        except Exception:
            exc_holder[0] = traceback.format_exc(limit=3)

    thread = threading.Thread(target=_compile, daemon=True)
    thread.start()
    thread.join(timeout_secs)
    if thread.is_alive():
        base.error = f"timeout after {timeout_secs}s (likely scanned/OCR-heavy)"
        return base
    if exc_holder[0]:
        base.error = exc_holder[0]
        return base

    ctx = ctx_holder[0]
    if ctx is None or ctx.document is None:
        base.error = "document is None after compile"
        return base

    if ctx.document is None:
        base.error = "document is None after compile"
        return base

    warnings = [i for i in ctx.validation.issues if i.code == "W_TABLE_EXPECTED_NOT_EXTRACTED"]
    base.warning_count = len(warnings)

    reports = ctx.document.metadata.get("table_expectation_reports", [])
    base.total_pages = len(reports)
    for r in reports:
        risk_sigs = [s["name"] for s in r.get("signals", []) if s["status"] == "risk"]
        risk_fam  = list({s["family"] for s in r.get("signals", []) if s["status"] == "risk"})
        page_warned = (
            r.get("expected") == "true"
            and r["page"] not in {
                b.page for b in ctx.document.blocks
                if getattr(b, "type", None) and b.type.name == "TABLE"
            }
        )
        pr = PageResult(
            page=r["page"],
            expected=r.get("expected", "false"),
            confidence=r.get("confidence", 0.0),
            warned=page_warned,
            risk_families=risk_fam,
            risk_signals=risk_sigs,
            extracted_table_count=len(r.get("extracted_table_block_ids", [])),
            rejected_candidate_count=len(r.get("rejected_candidates", [])),
        )
        if pr.expected == "true":
            base.pages_expected_true += 1
        elif pr.expected == "unknown":
            base.pages_expected_unknown += 1
        base.page_results.append(asdict(pr))

    return base


# ── Metrics ────────────────────────────────────────────────────────────────────

def _compute_metrics(records: list[RunRecord], label: str) -> dict:
    """Compute TP/FP/TN/FN for W_TABLE_EXPECTED_NOT_EXTRACTED.

    Positive label: doc has gt_class in ("missed", "partial")
    Prediction:     warning_count > 0

    Errored/timed-out docs are excluded from all counts.

    For strict positive definition, use only "missed" (complete miss).
    For broad positive, include "partial" (at least one table missed).
    """
    scoreable  = [r for r in records if not r.error]
    strict_pos = [r for r in scoreable if r.gt_class == "missed"]
    broad_pos  = [r for r in scoreable if r.gt_class in ("missed", "partial")]
    neg        = [r for r in scoreable if r.gt_class in ("extracted", "negative")]
    predicted  = [r for r in scoreable if r.warning_count > 0]

    pred_ids = {id(r) for r in predicted}
    neg_ids  = {id(r) for r in neg}

    def _stats(pos_list: list, name: str) -> dict:
        pos_ids = {id(r) for r in pos_list}
        tp = len(pos_ids & pred_ids)
        fn = len(pos_ids - pred_ids)
        fp = len(neg_ids & pred_ids)
        tn = len(neg_ids - pred_ids)
        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall    = tp / (tp + fn) if (tp + fn) > 0 else None
        specificity = tn / (tn + fp) if (tn + fp) > 0 else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision is not None and recall is not None
              and (precision + recall) > 0 else None)
        fpr = fp / (fp + tn) if (fp + tn) > 0 else None
        return {
            "definition": name,
            "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "specificity": round(specificity, 4) if specificity is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "fpr": round(fpr, 4) if fpr is not None else None,
            "pos_count": len(pos_list),
            "neg_count": len(neg),
        }

    return {
        "label": label,
        "strict": _stats(strict_pos, "gt_class=missed"),
        "broad": _stats(broad_pos, "gt_class=missed|partial"),
        "predicted_positive": len(predicted),
        "total": len(records),
    }


def _signal_family_metrics(records: list[RunRecord]) -> dict:
    """Per-signal-family firing rate on positive vs negative docs."""
    families = ["parser", "text", "content", "archetype"]
    pos = [r for r in records if r.gt_class in ("missed", "partial")]
    neg = [r for r in records if r.gt_class in ("extracted", "negative")]

    result = {}
    for fam in families:
        pos_fire = sum(1 for r in pos for pr in r.page_results if fam in pr["risk_families"])
        neg_fire = sum(1 for r in neg for pr in r.page_results if fam in pr["risk_families"])
        total_pos_pages = sum(len(r.page_results) for r in pos)
        total_neg_pages = sum(len(r.page_results) for r in neg)
        result[fam] = {
            "pos_fire": pos_fire,
            "neg_fire": neg_fire,
            "pos_fire_rate": round(pos_fire / total_pos_pages, 4) if total_pos_pages else None,
            "neg_fire_rate": round(neg_fire / total_neg_pages, 4) if total_neg_pages else None,
        }
    return result


def _gt_class_breakdown(records: list[RunRecord]) -> dict:
    buckets: dict[str, dict] = {}
    for r in records:
        b = buckets.setdefault(r.gt_class, {"total": 0, "warned": 0})
        b["total"] += 1
        if r.warning_count > 0:
            b["warned"] += 1
    for b in buckets.values():
        b["warn_rate"] = round(b["warned"] / b["total"], 4) if b["total"] else 0.0
    return buckets


def _false_positive_examples(records: list[RunRecord], n: int = 10) -> list[dict]:
    """Return n false-positive examples with signal details."""
    fps = [r for r in records if r.warning_count > 0 and r.gt_class in ("extracted", "negative")]
    fps.sort(key=lambda r: r.pages_expected_true, reverse=True)
    out = []
    for r in fps[:n]:
        out.append({
            "test_id": r.test_id,
            "category": r.category,
            "gt_class": r.gt_class,
            "grits_con": r.grits_con,
            "pages_expected_true": r.pages_expected_true,
            "page_signals": [
                {"page": pr["page"], "risk_signals": pr["risk_signals"],
                 "risk_families": pr["risk_families"]}
                for pr in r.page_results if pr["expected"] == "true"
            ],
        })
    return out


def _false_safe_families(records: list[RunRecord]) -> dict:
    """Per-family results for the four known false-safe document families."""
    families = {
        "fqr_retail_blackrock": "fqr-retail-blackrock",
        "VRSK_2012": "VRSK.2012",
        "SERFF_CA": "SERFF_CA",
        "FBLB_134215544": "FBLB-134215544",
    }
    result = {}
    for key, stem in families.items():
        matching = [r for r in records if stem.lower() in r.test_id.lower()]
        result[key] = {
            "docs_found": len(matching),
            "docs_warned": sum(1 for r in matching if r.warning_count > 0),
            "docs_expected_true": sum(1 for r in matching if r.pages_expected_true > 0),
            "total_warnings": sum(r.warning_count for r in matching),
        }
    return result


# ── Main entry point ───────────────────────────────────────────────────────────

@click.command()
@click.option("--neg-controls", default=150, show_default=True,
              help="Number of negative-control docs to sample (split across text/chart/layout).")
@click.option("--out", default=str(DEFAULT_OUT), show_default=True,
              help="Output JSON path.")
@click.option("--timeout", default=25, show_default=True,
              help="Per-doc wall-clock timeout in seconds (skip OCR-heavy scanned PDFs).")
@click.option("--verbose", is_flag=True, default=False)
def main(neg_controls: int, out: str, timeout: int, verbose: bool) -> None:
    """Run Phase 5 table-expectation detector validation."""

    click.echo("Loading ground truth from parsebench eval report...")
    table_records = _load_table_ground_truth()
    click.echo(f"  Table docs: {len(table_records)}")

    gt_breakdown: dict[str, int] = {}
    for r in table_records:
        gt_breakdown[r.gt_class] = gt_breakdown.get(r.gt_class, 0) + 1
    click.echo(f"  GT breakdown: {gt_breakdown}")

    click.echo(f"\nSampling {neg_controls} negative-control docs...")
    neg_records = _load_negative_controls(neg_controls, SEED)
    click.echo(f"  Negative controls: {len(neg_records)}")
    neg_cat: dict[str, int] = {}
    for r in neg_records:
        neg_cat[r.category] = neg_cat.get(r.category, 0) + 1
    click.echo(f"  Neg-control categories: {neg_cat}")

    all_records_meta = table_records + neg_records
    click.echo(f"\nTotal docs to compile: {len(all_records_meta)}")
    click.echo("Running compilation...")

    run_records: list[RunRecord] = []
    errors = 0
    timeouts = 0
    for i, doc in enumerate(all_records_meta):
        if verbose:
            click.echo(f"  [{i+1}/{len(all_records_meta)}] {doc.test_id}")
        elif (i + 1) % 50 == 0:
            click.echo(f"  {i+1}/{len(all_records_meta)} compiled, errors={errors}, timeouts={timeouts}")
        result = _run_doc(doc, timeout_secs=timeout)
        if result.error:
            if "timeout" in (result.error or "").lower():
                timeouts += 1
            else:
                errors += 1
            if verbose:
                click.echo(f"    ERROR: {result.error[:100]}", err=True)
        run_records.append(result)

    click.echo(f"\nCompilation complete. Errors: {errors}, Timeouts: {timeouts}, Total: {len(all_records_meta)}")

    # Split into table and neg subsets for metrics
    table_run  = [r for r in run_records if r.category == "table"]

    # Overall metrics (table docs + neg controls)
    metrics_all = _compute_metrics(run_records, "all_docs")
    # Table-only metrics
    metrics_table_only = _compute_metrics(table_run, "table_docs_only")

    breakdown = _gt_class_breakdown(run_records)
    family_metrics = _signal_family_metrics(run_records)
    fp_examples = _false_positive_examples(run_records)
    false_safe_families = _false_safe_families(run_records)

    # Scoring simulation: what if we add a 10/15 point penalty?
    def _simulate_score_impact(penalty: int) -> dict:
        warned_docs = [r for r in run_records if r.warning_count > 0]
        true_pos = [r for r in warned_docs if r.gt_class in ("missed", "partial")]
        false_pos = [r for r in warned_docs if r.gt_class in ("extracted", "negative")]
        return {
            "penalty": penalty,
            "docs_impacted": len(warned_docs),
            "true_positive_docs_impacted": len(true_pos),
            "false_positive_docs_impacted": len(false_pos),
        }

    scoring_simulations = [
        _simulate_score_impact(10),
        _simulate_score_impact(15),
        _simulate_score_impact(20),
    ]

    output = {
        "run_date": "2026-07-13",
        "per_doc_timeout_secs": timeout,
        "corpus": {
            "table_docs": len(table_records),
            "neg_control_docs": len(neg_records),
            "total": len(all_records_meta),
            "errors": errors,
            "timeouts": timeouts,
        },
        "ground_truth_breakdown": gt_breakdown,
        "metrics_all": metrics_all,
        "metrics_table_only": metrics_table_only,
        "gt_class_detection_rate": breakdown,
        "signal_family_metrics": family_metrics,
        "false_positive_examples": fp_examples,
        "false_safe_family_results": false_safe_families,
        "scoring_simulations": scoring_simulations,
        "records": [asdict(r) for r in run_records],
    }

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    click.echo(f"\nResults written to {out_path}")
    click.echo("\n=== SUMMARY ===")
    click.echo("Strict metrics (gt=missed only):")
    s = metrics_all["strict"]
    click.echo(f"  TP={s['tp']}, FN={s['fn']}, FP={s['fp']}, TN={s['tn']}")
    click.echo(f"  Precision={s['precision']}, Recall={s['recall']}, F1={s['f1']}, FPR={s['fpr']}")
    click.echo("\nBroad metrics (gt=missed|partial):")
    b = metrics_all["broad"]
    click.echo(f"  TP={b['tp']}, FN={b['fn']}, FP={b['fp']}, TN={b['tn']}")
    click.echo(f"  Precision={b['precision']}, Recall={b['recall']}, F1={b['f1']}, FPR={b['fpr']}")
    click.echo("\nGT-class detection rates:")
    for cls, stats in breakdown.items():
        click.echo(f"  {cls}: {stats['warned']}/{stats['total']} warned ({stats['warn_rate']:.1%})")
    click.echo("\nFalse-safe family coverage:")
    for fam, stats in false_safe_families.items():
        click.echo(f"  {fam}: {stats['docs_warned']}/{stats['docs_found']} warned")


if __name__ == "__main__":
    main()
