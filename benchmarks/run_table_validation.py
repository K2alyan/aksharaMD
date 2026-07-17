"""Table-quality signal validation against the parsebench corpus.

Stratifies parsebench table examples by grits_con score, compiles a
stratified sample through AksharaMD (which runs the table_quality
validator), and writes a baseline JSON report with per-signal stats.

Usage:
    cd C:/Users/kalya/omnimark
    python -m benchmarks.run_table_validation [--samples-per-bucket N] [--out PATH]

Output:
    benchmarks/table_quality_baseline_run.json
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import click

PARSEBENCH_ROOT = Path("C:/Users/kalya/parsebench")
EVAL_REPORT = PARSEBENCH_ROOT / "output/aksharamd_parse/_evaluation_report.json"
DOCS_DIR = PARSEBENCH_ROOT / "data/docs/table"
OMNIMARK_ROOT = Path(__file__).parent.parent
DEFAULT_OUT = OMNIMARK_ROOT / "benchmarks/table_quality_baseline_run.json"

SEED = 42


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class ExampleRecord:
    test_id: str
    pdf_path: str
    grits_con: float
    bucket: str            # "good", "mid", "partial"
    tables_found_expected: int
    tables_found_actual: int


@dataclass
class TableRecord:
    """Per-table output row."""
    test_id: str
    grits_con: float
    bucket: str
    table_index: int
    extraction_method: str | None
    row_count: int
    column_count: int
    overall_status: str
    signals: list[dict]   # raw signal dicts
    findings: list[dict]  # finding dicts
    error: str | None = None


# ── Corpus loading ─────────────────────────────────────────────────────────────

def _load_table_examples() -> list[ExampleRecord]:
    with open(EVAL_REPORT, encoding="utf-8") as f:
        data = json.load(f)
    examples = data.get("per_example_results", data.get("examples", []))
    records: list[ExampleRecord] = []
    for e in examples:
        if "table" not in e.get("tags", []):
            continue
        grits_con = None
        tables_found_expected = 0
        tables_found_actual = 0
        for m in e.get("metrics", []):
            if m["metric_name"] == "grits_con":
                grits_con = m["value"]
                meta = m.get("metadata", {})
                tables_found_expected = meta.get("tables_found_expected", 0)
                tables_found_actual = meta.get("tables_found_actual", 0)
                break
        if grits_con is None:
            continue
        test_id = e["test_id"]
        doc_name = test_id.split("/", 1)[-1] + ".pdf"
        pdf_path = DOCS_DIR / doc_name
        if not pdf_path.exists():
            continue
        if grits_con >= 0.8:
            bucket = "good"
        elif grits_con >= 0.5:
            bucket = "mid"
        elif grits_con > 0:
            bucket = "partial"
        else:
            bucket = "zero"
        records.append(ExampleRecord(
            test_id=test_id,
            pdf_path=str(pdf_path),
            grits_con=grits_con,
            bucket=bucket,
            tables_found_expected=tables_found_expected,
            tables_found_actual=tables_found_actual,
        ))
    return records


def _stratified_sample(
    records: list[ExampleRecord],
    samples_per_bucket: int,
    rng: random.Random,
) -> list[ExampleRecord]:
    result: list[ExampleRecord] = []
    for bucket in ("good", "mid", "partial"):
        pool = [r for r in records if r.bucket == bucket]
        n = min(samples_per_bucket, len(pool))
        result.extend(rng.sample(pool, n))
    return result


# ── Compilation ────────────────────────────────────────────────────────────────

def _compile_one(record: ExampleRecord) -> list[TableRecord]:
    sys.path.insert(0, str(OMNIMARK_ROOT))
    from aksharamd.compiler import Compiler
    from aksharamd.models.block import BlockType
    from aksharamd.scoring.table_findings import aggregate_findings
    from aksharamd.scoring.table_quality import TableQualityReport

    table_records: list[TableRecord] = []
    try:
        with tempfile.TemporaryDirectory() as td:
            ctx = Compiler(output_dir=td).compile(record.pdf_path)
            if ctx.document is None:
                issues = [str(i) for i in ctx.validation.issues]
                return [TableRecord(
                    test_id=record.test_id,
                    grits_con=record.grits_con,
                    bucket=record.bucket,
                    table_index=0,
                    extraction_method=None,
                    row_count=0,
                    column_count=0,
                    overall_status="compile_error",
                    signals=[],
                    findings=[],
                    error="; ".join(issues),
                )]

            reports_raw = ctx.document.metadata.get("table_quality_reports", [])
            if not reports_raw:
                return [TableRecord(
                    test_id=record.test_id,
                    grits_con=record.grits_con,
                    bucket=record.bucket,
                    table_index=0,
                    extraction_method=None,
                    row_count=0,
                    column_count=0,
                    overall_status="no_tables_extracted",
                    signals=[],
                    findings=[],
                    error=None,
                )]

            for i, rpt_dict in enumerate(reports_raw):
                rpt = TableQualityReport.model_validate(rpt_dict)
                findings = aggregate_findings(rpt)
                table_records.append(TableRecord(
                    test_id=record.test_id,
                    grits_con=record.grits_con,
                    bucket=record.bucket,
                    table_index=i,
                    extraction_method=rpt.extraction_method,
                    row_count=rpt.row_count,
                    column_count=rpt.column_count,
                    overall_status=rpt.overall_status,
                    signals=[s.model_dump() for s in rpt.signals],
                    findings=[f.model_dump() for f in findings],
                    error=None,
                ))
    except Exception as exc:
        table_records.append(TableRecord(
            test_id=record.test_id,
            grits_con=record.grits_con,
            bucket=record.bucket,
            table_index=0,
            extraction_method=None,
            row_count=0,
            column_count=0,
            overall_status="exception",
            signals=[],
            findings=[],
            error=traceback.format_exc()[-400:],
        ))
    return table_records


# ── Signal statistics ──────────────────────────────────────────────────────────

def _signal_stats(table_rows: list[TableRecord]) -> dict:
    """Per-signal firing rate by bucket and confusion matrix vs grits label."""
    all_signal_names: list[str] = []
    for row in table_rows:
        for s in row.signals:
            n = s["name"]
            if n not in all_signal_names:
                all_signal_names.append(n)

    all_finding_names: list[str] = []
    for row in table_rows:
        for f in row.findings:
            n = f["name"]
            if n not in all_finding_names:
                all_finding_names.append(n)

    signal_stats: dict[str, dict] = {}
    for sig_name in all_signal_names:
        by_bucket: dict[str, dict] = {}
        tp = fp = tn = fn = 0
        for row in table_rows:
            if row.overall_status in ("compile_error", "exception", "no_tables_extracted"):
                continue
            sig = next((s for s in row.signals if s["name"] == sig_name), None)
            fires = sig is not None and sig["status"] == "risk"
            is_poor = row.grits_con < 0.5
            if fires and is_poor:
                tp += 1
            elif fires and not is_poor:
                fp += 1
            elif not fires and is_poor:
                fn += 1
            else:
                tn += 1
            bucket = row.bucket
            by_bucket.setdefault(bucket, {"fires": 0, "total": 0})
            by_bucket[bucket]["total"] += 1
            if fires:
                by_bucket[bucket]["fires"] += 1
        total = tp + fp + tn + fn
        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall = tp / (tp + fn) if (tp + fn) > 0 else None
        fpr = fp / (fp + tn) if (fp + tn) > 0 else None
        signal_stats[sig_name] = {
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "total_evaluated": total,
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
            "false_positive_rate": round(fpr, 3) if fpr is not None else None,
            "by_bucket": by_bucket,
        }

    finding_stats: dict[str, dict] = {}
    for finding_name in all_finding_names:
        tp = fp = tn = fn = 0
        by_bucket: dict[str, dict] = {}
        for row in table_rows:
            if row.overall_status in ("compile_error", "exception", "no_tables_extracted"):
                continue
            fnd = next((f for f in row.findings if f["name"] == finding_name), None)
            if fnd is None or fnd["status"] == "not_applicable":
                continue
            fires = fnd["status"] == "risk"
            is_poor = row.grits_con < 0.5
            if fires and is_poor:
                tp += 1
            elif fires and not is_poor:
                fp += 1
            elif not fires and is_poor:
                fn += 1
            else:
                tn += 1
            bucket = row.bucket
            by_bucket.setdefault(bucket, {"fires": 0, "total": 0})
            by_bucket[bucket]["total"] += 1
            if fires:
                by_bucket[bucket]["fires"] += 1
        total = tp + fp + tn + fn
        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall = tp / (tp + fn) if (tp + fn) > 0 else None
        fpr = fp / (fp + tn) if (fp + tn) > 0 else None
        finding_stats[finding_name] = {
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "total_evaluated": total,
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
            "false_positive_rate": round(fpr, 3) if fpr is not None else None,
            "by_bucket": by_bucket,
        }

    return {"signals": signal_stats, "findings": finding_stats}


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--samples-per-bucket", default=15, show_default=True,
              help="Documents to compile per grits bucket (good/mid/partial)")
@click.option("--out", default=str(DEFAULT_OUT), show_default=True,
              help="Output JSON path")
@click.option("--seed", default=SEED, show_default=True)
def main(samples_per_bucket: int, out: str, seed: int) -> None:
    rng = random.Random(seed)
    print("Loading parsebench corpus...", flush=True)
    all_records = _load_table_examples()
    bucket_counts = {}
    for r in all_records:
        bucket_counts[r.bucket] = bucket_counts.get(r.bucket, 0) + 1
    print(f"  Corpus: {len(all_records)} table docs with PDFs present")
    print(f"  Buckets: {bucket_counts}")

    sample = _stratified_sample(all_records, samples_per_bucket, rng)
    sample_counts = {}
    for r in sample:
        sample_counts[r.bucket] = sample_counts.get(r.bucket, 0) + 1
    print(f"  Sample: {len(sample)} docs — {sample_counts}", flush=True)

    print(f"Compiling {len(sample)} documents...", flush=True)
    all_table_rows: list[TableRecord] = []
    for i, record in enumerate(sample, 1):
        print(f"  [{i:3d}/{len(sample)}] {record.test_id} (grits={record.grits_con:.3f})", flush=True)
        rows = _compile_one(record)
        all_table_rows.extend(rows)
        for r in rows:
            status_tag = r.overall_status
            findings_risk = [f["name"] for f in r.findings if f["status"] == "risk"]
            print(f"           -> tables={len([x for x in rows if x.table_index >= 0])} "
                  f"status={status_tag} "
                  f"risk_findings={findings_risk or '[]'}", flush=True)

    print(f"\nComputed {len(all_table_rows)} table quality records.", flush=True)

    stats = _signal_stats(all_table_rows)

    output = {
        "schema_version": "1.0",
        "samples_per_bucket": samples_per_bucket,
        "seed": seed,
        "corpus_bucket_counts": bucket_counts,
        "sample_bucket_counts": sample_counts,
        "total_table_records": len(all_table_rows),
        "signal_stats": stats["signals"],
        "finding_stats": stats["findings"],
        "table_records": [asdict(r) for r in all_table_rows],
    }

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\nWrote {out_path}", flush=True)

    # Print summary table
    print("\n=== FINDING STATS (against grits < 0.5 as 'poor') ===")
    print(f"{'Finding':<40} {'TP':>4} {'FP':>4} {'TN':>4} {'FN':>4} {'Prec':>6} {'Recall':>7} {'FPR':>6}")
    print("-" * 80)
    for fname, fstat in sorted(stats["findings"].items()):
        print(f"{fname:<40} {fstat['tp']:>4} {fstat['fp']:>4} {fstat['tn']:>4} {fstat['fn']:>4} "
              f"{str(fstat['precision'] or '-'):>6} {str(fstat['recall'] or '-'):>7} "
              f"{str(fstat['false_positive_rate'] or '-'):>6}")

    print("\n=== SIGNAL STATS (top signals by FPR desc) ===")
    print(f"{'Signal':<45} {'FPR':>6} {'TP':>4} {'FP':>4}")
    print("-" * 65)
    sig_rows = sorted(
        stats["signals"].items(),
        key=lambda x: x[1].get("false_positive_rate") or 0,
        reverse=True,
    )
    for sname, sstat in sig_rows[:20]:
        fpr = sstat.get("false_positive_rate")
        print(f"{sname:<45} {str(fpr or '-'):>6} {sstat['tp']:>4} {sstat['fp']:>4}")


if __name__ == "__main__":
    main()
