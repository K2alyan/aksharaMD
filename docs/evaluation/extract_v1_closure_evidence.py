"""Extract selected existing evidence; never execute or overwrite a benchmark.

Usage: python docs/evaluation/extract_v1_closure_evidence.py EVIDENCE_ROOT OUTPUT
EVIDENCE_ROOT must contain the original local artifacts named below. A checkout
alone does not contain those artifacts. Hashes identify inspected bytes, not
historical producer commits. OUTPUT must be a new file.
"""

import hashlib
import json
import sys
from pathlib import Path

SOURCES = {
    "olmocr": (
        "benchmarks/results/stage2-olmocr-2026-09-22-aggregated.json",
        ["generated_at", "provenance_note", "n_total_result_files", "n_total_results",
         "n_duplicate_result_files_removed", "n_scored", "completeness",
         "claim_1_detector_precision_recall", "claim_2_spearman_rho",
         "track_b_allocation_manifest_path"],
    ),
    "doclaynet": (
        "benchmarks/results/stage2-doclaynet-2026-09-20-aggregated.json",
        ["aggregated_utc", "n_result_files", "n_executed", "n_defect", "per_parser"],
    ),
    "track_c": (
        "benchmarks/results/stage2-track-c-2026-09-21-aggregated.json",
        ["aggregated_at", "primary_analysis", "exploratory_analysis",
         "mmlong_bench_doc", "claim4_v1_verdict", "claim4_v1_verdict_reasons"],
    ),
    "selector_reconciliation": (
        "docs/research/product-direction-2026-09-21/independent/eval-selector-reconciliation.json",
        ["parser_only_oracle", "all_arms_oracle"],
    ),
}


def extract(root):
    result = {
        "format_version": 1,
        "boundary": "Selected existing aggregate fields, not a rerun or proof of historical producer identity.",
        "sources": {},
    }
    for name, (relative, fields) in SOURCES.items():
        raw = (root / relative).read_bytes()
        original = json.loads(raw)
        selected = {key: original[key] for key in fields}
        if name == "olmocr":
            selected["completeness"] = {
                key: value for key, value in selected["completeness"].items()
                if not isinstance(value, list)
            }
        if name == "doclaynet":
            selected["per_parser"] = {
                parser: {key: value for key, value in row.items() if key != "by_doc_category"}
                for parser, row in selected["per_parser"].items()
            }
        result["sources"][name] = {
            "local_source_path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "selected_fields": selected,
        }
    return result


if __name__ == "__main__":
    with Path(sys.argv[2]).open("x", encoding="utf-8", newline="\n") as output:
        json.dump(extract(Path(sys.argv[1])), output, indent=2, allow_nan=False)
        output.write("\n")
