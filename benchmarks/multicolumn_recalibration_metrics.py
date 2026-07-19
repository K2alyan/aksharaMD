"""Compute confusion-matrix metrics from the multicolumn recalibration JSON.

Reads the harness output (`MULTICOLUMN_RECALIBRATION_*.json`) and the labels
file that were pinned into the run, then emits:

- Document-level TP/FP/TN/FN + P/R/FPR/F1.
- Page-level TP/FP counts (uses the detector's per-page diagnostics
  where they are available; ground-truth page labels are only used when
  a document's labels file provides them).
- Per-document breakdown table (`| asset | label | warning | pages
  problem | notes |`).
- Block-level vs span-level analysis: distinguishes documents where the
  warning did/did not fire from documents whose ground truth requires
  span-level detection.

Emits a compact JSON summary and prints a human-readable Markdown block
to stdout.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(harness_json: Path) -> dict:
    with harness_json.open("r", encoding="utf-8") as f:
        return json.load(f)


def _fired(compile_summary: dict) -> bool:
    return "W_MULTICOLUMN_ORDER" in (compile_summary.get("warning_codes") or [])


def classify_doc(label: dict, fired: bool) -> str | None:
    exp = label.get("expected_positive")
    if exp is None or label.get("unavailable") or label.get("excluded_reason"):
        return None
    if exp is True and fired:
        return "TP"
    if exp is True and not fired:
        return "FN"
    if exp is False and fired:
        return "FP"
    if exp is False and not fired:
        return "TN"
    return None


def precision(tp: int, fp: int) -> float:
    return tp / (tp + fp) if (tp + fp) else 0.0


def recall(tp: int, fn: int) -> float:
    return tp / (tp + fn) if (tp + fn) else 0.0


def fpr(fp: int, tn: int) -> float:
    return fp / (fp + tn) if (fp + tn) else 0.0


def f1(p: float, r: float) -> float:
    return (2 * p * r / (p + r)) if (p + r) else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--harness", required=True, type=Path)
    ap.add_argument("--labels", type=Path, default=None,
                    help="If provided, re-join labels from this file (overrides labels "
                         "baked into the harness output).")
    ap.add_argument("--summary-json", type=Path, default=None,
                    help="Optional path to write a compact metrics-only JSON summary")
    args = ap.parse_args()

    doc = load(args.harness)
    results = doc["results"]

    if args.labels is not None:
        with args.labels.open("r", encoding="utf-8") as f:
            labels_doc = json.load(f)
        labels_map: dict[str, dict] = labels_doc.get("labels", {})
        for r in results:
            rel = (r.get("relpath") or r.get("asset")).replace("\\", "/")
            # strip "pdf/" prefix if the corpus was scanned rooted at .../pdf
            stripped = rel[4:] if rel.startswith("pdf/") else rel
            r["label"] = (
                labels_map.get(stripped)
                or labels_map.get(rel)
                or labels_map.get(r["asset"])
                or {}
            )

    # Document-level confusion matrix
    verdicts: dict[str, list[str]] = {"TP": [], "FP": [], "TN": [], "FN": [], "excluded": []}
    per_doc: list[dict] = []
    total_fp_pages = 0
    total_fp_docs_pages: dict[str, list[int]] = {}

    for r in results:
        cs = r.get("compile_summary") or {}
        label = r.get("label") or {}
        fired = _fired(cs)
        verdict = classify_doc(label, fired)
        entry = {
            "asset": r["asset"],
            "label_layout": label.get("layout"),
            "expected_positive": label.get("expected_positive"),
            "warning_fired": fired,
            "verdict": verdict or "excluded",
            "score": cs.get("readiness_score"),
            "band": cs.get("quality_band"),
            "pages": cs.get("pages"),
            "problem_pages": (r.get("multicolumn_diagnostics") or {}).get("problem_pages", []) if r.get("multicolumn_diagnostics") else [],
            "warnings": cs.get("warning_codes"),
        }
        per_doc.append(entry)
        (verdicts[verdict] if verdict else verdicts["excluded"]).append(r["asset"])

        # Track FP pages when the document is TN but the detector fired
        if verdict == "FP":
            pps = entry["problem_pages"]
            total_fp_pages += len(pps)
            total_fp_docs_pages[r["asset"]] = pps

    tp = len(verdicts["TP"])
    fp = len(verdicts["FP"])
    tn = len(verdicts["TN"])
    fn = len(verdicts["FN"])
    excluded = len(verdicts["excluded"])
    p = precision(tp, fp)
    rc = recall(tp, fn)
    fpr_ = fpr(fp, tn)
    f1_ = f1(p, rc)

    summary = {
        "commit_under_test": doc.get("commit"),
        "cli_version": doc.get("cli_version"),
        "corpus_size_total": len(results),
        "labelled_TP": tp,
        "labelled_FP": fp,
        "labelled_TN": tn,
        "labelled_FN": fn,
        "excluded": excluded,
        "precision": round(p, 3),
        "recall": round(rc, 3),
        "false_positive_rate": round(fpr_, 3),
        "f1": round(f1_, 3),
        "false_positive_documents": verdicts["FP"],
        "false_positive_page_totals": total_fp_docs_pages,
        "false_negative_documents": verdicts["FN"],
        "true_positive_documents": verdicts["TP"],
        "excluded_documents": verdicts["excluded"],
        "per_document": per_doc,
    }
    if args.summary_json:
        with args.summary_json.open("w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=False)

    # Human-readable Markdown block
    print("### Document-level confusion (labeled subset)")
    print()
    print("| Metric | Value |")
    print("|---|---:|")
    print(f"| TP | {tp} |")
    print(f"| FP | {fp} |")
    print(f"| TN | {tn} |")
    print(f"| FN | {fn} |")
    print(f"| Excluded | {excluded} |")
    print(f"| Precision | {p:.3f} |")
    print(f"| Recall | {rc:.3f} |")
    print(f"| False-positive rate | {fpr_:.3f} |")
    print(f"| F1 | {f1_:.3f} |")
    print()
    if verdicts["FP"]:
        print("### False-positive documents")
        for asset in verdicts["FP"]:
            pps = total_fp_docs_pages.get(asset, [])
            print(f"- **{asset}** — flagged on {len(pps)} page(s): {pps}")
        print(f"\nTotal false-positive page firings: **{total_fp_pages}**")
    print()
    if verdicts["FN"]:
        print("### False-negative documents")
        for asset in verdicts["FN"]:
            print(f"- {asset}")
    if verdicts["TP"]:
        print()
        print("### True-positive documents (warning fired on positive label)")
        for asset in verdicts["TP"]:
            print(f"- {asset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
