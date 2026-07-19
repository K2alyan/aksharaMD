"""Multicolumn recalibration for Issue #50 — ParseBench + frozen public
corpus, page + document + observable slices.

Consumes only reviewer-approved evidence. No detector, parser, or
scoring code is modified. The four rule variants (baseline, C3, C4,
C3+C4) are imported unchanged from ``multicolumn_candidate_replay``.

Two corpora:

1. **ParseBench** — 12 assets from ``parsebench_assets.lock.json``.
   Each PDF is compiled once via ``aksharamd compile`` against the
   local cache at ``%LOCALAPPDATA%\\aksharamd\\parsebench\\<revision>``
   (no network). The compile output's
   ``multicolumn_diagnostics.page_analyses`` is the per-page signals
   record used by the RULES.
2. **Public corpus (frozen)** — reused as-is from
   ``benchmarks/MULTICOLUMN_RECALIBRATION_2026-07-18.json``. No
   recompile. If that artifact is missing or its shape drifts, the
   harness fails loudly.

Metrics slices per corpus:

- ``doc_historical`` — every attested ``expected_label`` participates.
- ``doc_reviewer_confirmed`` (ParseBench only) — ambiguous +
  non-multicolumn assets are excluded.
- ``page`` (ParseBench only) — ambiguous pages excluded;
  non-multicolumn assets excluded entirely.
- ``observable`` (ParseBench only) — page slice restricted to
  ``detector_observability == "block-level-observable"``.

Refuses to run if:

- The ParseBench cache is not populated with matching sha256/size.
- The frozen public-corpus artifact is missing or its
  ``harness_version`` disagrees with what this script expects.

The script is offline. It does NOT fetch anything from the network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404 - harness orchestrates the aksharamd CLI
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Reuse the frozen rule set from Phase 2 unchanged.
from benchmarks.multicolumn_candidate_replay import (  # type: ignore
    RULES,
    _baseline_gap_gate,
    _c3_gap_gate,
    _htr,
    _sf,
    _ymt,
)
from benchmarks.parsebench_recalibration_metrics import (
    confusion,
    parsebench_doc_historical,
    parsebench_doc_reviewer_confirmed,
    parsebench_observable_eligibility,
    parsebench_page_eligibility,
    public_doc_historical,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCKFILE = _REPO_ROOT / "benchmarks" / "parsebench_assets.lock.json"
_PUBLIC_LABELS = _REPO_ROOT / "benchmarks" / "multicolumn_recalibration_labels.json"
_PUBLIC_FROZEN_RESULTS = _REPO_ROOT / "benchmarks" / "MULTICOLUMN_RECALIBRATION_2026-07-18.json"
_EXPECTED_PUBLIC_HARNESS_VERSION = "1"
_EXPECTED_PUBLIC_COMMIT = "c4dfe86bb391727b5eef9ddd28bfd215d1c554c2"


# ── Cache access ─────────────────────────────────────────────────────────


def _parsebench_cache_dir(revision: str) -> Path:
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        raise RuntimeError("LOCALAPPDATA is unset; ParseBench cache lookup requires it")
    return Path(la) / "aksharamd" / "parsebench" / revision


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_cache(assets: list[dict], cache_dir: Path) -> None:
    """Fails loudly if any asset's cached bytes don't match the
    promoted sha256/size.
    """
    missing: list[str] = []
    for e in assets:
        p = cache_dir / f"{e['id']}.pdf"
        if not p.exists():
            missing.append(f"{e['id']}: {p} missing")
            continue
        size = p.stat().st_size
        if size != e["size_bytes"]:
            missing.append(
                f"{e['id']}: size {size} != promoted {e['size_bytes']}"
            )
            continue
        actual = _sha256(p)
        if actual != e["sha256"]:
            missing.append(
                f"{e['id']}: sha256 {actual} != promoted {e['sha256']}"
            )
    if missing:
        raise RuntimeError(
            "ParseBench cache verification failed:\n  " + "\n  ".join(missing)
        )


# ── Compile ──────────────────────────────────────────────────────────────


def _compile_one(pdf: Path, workdir: Path) -> dict[str, Any]:
    """Shell out to ``aksharamd compile`` and return diagnostics.

    Mirrors ``benchmarks/multicolumn_recalibration.py``: uses
    ``--json --quiet`` (stdout carries the summary) and reads
    ``<out_dir>/<stem>/document.json`` for the multicolumn diagnostics.
    """
    import shutil
    stem = pdf.stem
    out_dir = workdir / stem
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    t0 = time.perf_counter()
    binary = shutil.which("aksharamd")
    if binary is None:
        raise RuntimeError(
            "aksharamd binary not found on PATH; install the wheel before running "
            "this harness (`pip install -e .` or `pip install aksharamd`)"
        )
    proc = subprocess.run(  # nosec B603 - binary resolved via shutil.which; args are local paths
        [binary, "compile", str(pdf), "-o", str(out_dir), "--json", "--quiet"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        raise RuntimeError(
            f"compile failed for {pdf.name}: exit={proc.returncode}\n"
            f"stdout: {proc.stdout[-1000:]}\nstderr: {proc.stderr[-1000:]}"
        )
    doc_json_path = out_dir / stem / "document.json"
    if not doc_json_path.exists():
        raise RuntimeError(
            f"compile finished but document.json missing at {doc_json_path}"
        )
    with doc_json_path.open("r", encoding="utf-8") as f:
        document = json.load(f)
    meta = document.get("metadata") or {}
    diag = meta.get("multicolumn_diagnostics") or {}
    return {
        "multicolumn_diagnostics": diag,
        "_compile_elapsed_s": round(elapsed, 3),
    }


# ── Public-corpus intake ─────────────────────────────────────────────────


def _load_public_frozen() -> tuple[dict, dict]:
    if not _PUBLIC_FROZEN_RESULTS.exists():
        raise RuntimeError(
            f"frozen public-corpus artifact missing: {_PUBLIC_FROZEN_RESULTS}. "
            "This PR requires the phase-1 frozen output to be present unmodified."
        )
    with _PUBLIC_FROZEN_RESULTS.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    with _PUBLIC_LABELS.open("r", encoding="utf-8") as f:
        labels = json.load(f)
    return doc, labels


def _public_label_key(result: dict) -> str:
    rel = (result.get("relpath") or result.get("asset") or "").replace("\\", "/")
    if rel.startswith("pdf/"):
        rel = rel[4:]
    return rel


def _public_records(frozen: dict, labels_doc: dict) -> list[dict[str, Any]]:
    """Yield one record per public-corpus result. Records carry the
    per-page signals (unmodified) plus an ``expected_positive`` field
    derived from the labels manifest via the same fallback chain phase
    2 used.

    ``expected_positive`` is ``None`` when the label author refused to
    attest a verdict (image-only / encrypted / sparse-text).
    """
    labels_map: dict = labels_doc.get("labels", {})
    out: list[dict[str, Any]] = []
    for r in frozen["results"]:
        key = _public_label_key(r)
        # Fallback chain: stripped → full relpath → asset basename
        full_rel = (r.get("relpath") or "").replace("\\", "/")
        entry = (
            labels_map.get(key)
            or labels_map.get(full_rel)
            or labels_map.get(r["asset"])
            or {}
        )
        expected = entry.get("expected_positive")  # True / False / None
        diag = r.get("multicolumn_diagnostics") or {}
        analyses = diag.get("page_analyses") or []
        out.append({
            "id": key,
            "asset_basename": r["asset"],
            "expected_positive": expected,
            "excluded_reason": entry.get("excluded_reason", ""),
            "page_analyses": analyses,
            "baseline_warned": bool(diag.get("warned")),
        })
    return out


# ── ParseBench intake ────────────────────────────────────────────────────


def _parsebench_records(lockfile: dict, cache_dir: Path, workdir: Path) -> list[dict[str, Any]]:
    """Compile every ParseBench asset and return one record per asset."""
    out: list[dict[str, Any]] = []
    for e in lockfile["assets"]:
        aid = e["id"]
        pdf = cache_dir / f"{aid}.pdf"
        manifest = _compile_one(pdf, workdir)
        diag = manifest.get("multicolumn_diagnostics") or {}
        analyses = diag.get("page_analyses") or []
        out.append({
            "id": aid,
            "asset_basename": e["filename"],
            "expected_label": e.get("expected_label"),
            "defect_kind": e.get("defect_kind"),
            "page_ground_truth": e.get("page_level_ground_truth"),
            "page_analyses": analyses,
            "baseline_warned": bool(diag.get("warned")),
            "compile_elapsed_s": manifest.get("_compile_elapsed_s"),
        })
    return out


# ── Rule application ────────────────────────────────────────────────────


def _apply_rules(page_analyses: list[dict]) -> dict[str, Any]:
    """Return {rule_name: doc_verdict} plus the per-page verdicts."""
    doc: dict[str, bool] = {}
    pages: dict[str, list[bool]] = {}
    for name, fn in RULES:
        pages[name] = [bool(fn(a)) for a in page_analyses]
        doc[name] = any(pages[name])
    return {"document": doc, "pages": pages}


def _summarise_signals(a: dict) -> dict[str, Any]:
    return {
        "gap_size": a.get("gap_size"),
        "gap_rel": a.get("gap_rel"),
        "transition_rate": a.get("transition_rate"),
        "large_y_drops": a.get("large_y_drops"),
        "short_frac": a.get("short_frac"),
        "htr": _htr(a),
        "ymt": _ymt(a),
        "sf": _sf(a),
        "baseline_gap_gate": _baseline_gap_gate(a),
        "c3_gap_gate": _c3_gap_gate(a),
    }


# ── Metric assembly ─────────────────────────────────────────────────────


def _confusion_for(
    verdicts_by_id: dict[str, bool],
    expected_by_id: dict[str, bool],
) -> dict[str, Any]:
    """Build the confusion for the ids present in ``expected_by_id``."""
    rows = [
        (aid, expected_by_id[aid], verdicts_by_id[aid])
        for aid in sorted(expected_by_id)
        if aid in verdicts_by_id
    ]
    return confusion(rows)


def _all_slices(
    pb_records: list[dict],
    pub_records: list[dict],
    lockfile: dict,
) -> dict[str, Any]:
    """Compute every metric slice for every rule."""
    # ParseBench per-asset per-rule verdicts.
    pb_rule_verdicts: dict[str, dict[str, bool]] = {name: {} for name, _ in RULES}
    pb_rule_page_verdicts: dict[str, dict[tuple[str, int], bool]] = {name: {} for name, _ in RULES}
    for rec in pb_records:
        applied = _apply_rules(rec["page_analyses"])
        for name, _ in RULES:
            pb_rule_verdicts[name][rec["id"]] = applied["document"][name]
            for i, pv in enumerate(applied["pages"][name], start=1):
                pb_rule_page_verdicts[name][(rec["id"], i)] = pv

    # Public per-doc per-rule verdicts.
    pub_rule_verdicts: dict[str, dict[str, bool]] = {name: {} for name, _ in RULES}
    for rec in pub_records:
        applied = _apply_rules(rec["page_analyses"])
        for name, _ in RULES:
            pub_rule_verdicts[name][rec["id"]] = applied["document"][name]

    # ParseBench eligibility maps.
    pb_hist = parsebench_doc_historical(lockfile)  # {id: bool}
    pb_conf = parsebench_doc_reviewer_confirmed(lockfile)  # {id: bool}
    pb_pages = parsebench_page_eligibility(lockfile)  # list of rows
    pb_obs = parsebench_observable_eligibility(lockfile)  # list of rows

    pb_pages_expected: dict[tuple[str, int], bool] = {
        (r["asset"], r["page"]): r["expected_positive"] for r in pb_pages
    }
    pb_obs_expected: dict[tuple[str, int], bool] = {
        (r["asset"], r["page"]): r["expected_positive"] for r in pb_obs
    }

    # Public-corpus eligibility: iterate the resolved records (not the raw
    # labels_map) so we honour the fallback-chain resolution used by the
    # frozen phase-2 harness. This is why phase 2 got 22 attested docs
    # from a labels_map with 25 attested keys — some are alias aliases.
    pub_hist: dict[str, bool] = {
        rec["id"]: bool(rec["expected_positive"])
        for rec in pub_records
        if rec["expected_positive"] in (True, False)
    }

    slices: dict[str, dict[str, Any]] = {}
    for name, _ in RULES:
        slices[name] = {
            "parsebench": {
                "doc_historical": _confusion_for(pb_rule_verdicts[name], {k: bool(v) for k, v in pb_hist.items()}),
                "doc_reviewer_confirmed": _confusion_for(pb_rule_verdicts[name], pb_conf),
                "page": _confusion_for(
                    {f"{a}#{p}": pb_rule_page_verdicts[name][(a, p)] for (a, p) in pb_pages_expected},
                    {f"{a}#{p}": exp for (a, p), exp in pb_pages_expected.items()},
                ),
                "observable": _confusion_for(
                    {f"{a}#{p}": pb_rule_page_verdicts[name][(a, p)] for (a, p) in pb_obs_expected},
                    {f"{a}#{p}": exp for (a, p), exp in pb_obs_expected.items()},
                ),
            },
            "public_frozen": {
                "doc_historical": _confusion_for(pub_rule_verdicts[name], pub_hist),
            },
            "combined": {
                # Combined-document historical view — no page-level for the public set.
                "doc_historical": _confusion_for(
                    {**pb_rule_verdicts[name], **pub_rule_verdicts[name]},
                    {**{k: bool(v) for k, v in pb_hist.items()}, **pub_hist},
                ),
            },
        }
    return {
        "slices": slices,
        "pb_rule_verdicts": pb_rule_verdicts,
        "pb_rule_page_verdicts": pb_rule_page_verdicts,
        "pub_rule_verdicts": pub_rule_verdicts,
        "eligibility": {
            "parsebench_doc_historical_ids": sorted(pb_hist),
            "parsebench_doc_reviewer_confirmed_ids": sorted(pb_conf),
            "parsebench_page_ids": sorted(f"{a}#{p}" for (a, p) in pb_pages_expected),
            "parsebench_observable_ids": sorted(f"{a}#{p}" for (a, p) in pb_obs_expected),
            "public_doc_historical_ids": sorted(pub_hist),
        },
    }


# ── Expanded changed-decision log ───────────────────────────────────────


_CANDIDATE_REASONS = {
    "C3": "Raise gap-gate threshold from gap_rel>=0.15 to gap_rel>=0.30",
    "C4": "Require HTR AND (YMT OR SF); reject single-signal warnings",
    "C3+C4": "Raise gap gate to gap_rel>=0.30 AND require HTR AND (YMT OR SF)",
}


def _asset_exclusion_reason(lockfile: dict, aid: str) -> str:
    for e in lockfile["assets"]:
        if e["id"] != aid:
            continue
        gt = e.get("page_level_ground_truth") or {}
        pages = gt.get("pages") or []
        if any(p.get("extraction_status") == "ambiguous" for p in pages):
            return "page_level_ground_truth_ambiguous"
        if e.get("defect_kind") == "non-multicolumn":
            return "defect_kind_non_multicolumn"
        if e.get("expected_label") not in ("true-positive", "true-negative"):
            return "expected_label_unattested"
        return ""
    return "asset_not_in_lockfile"


def _pb_eligibility_label(
    pb_hist: dict[str, bool],
    pb_conf: dict[str, bool],
    pb_pages_ids: set[tuple[str, int]],
    pb_obs_ids: set[tuple[str, int]],
    aid: str,
    page: int | None,
) -> str:
    tags = []
    if aid in pb_conf:
        tags.append("reviewer_confirmed")
    elif aid in pb_hist:
        tags.append("historical_only")
    if page is not None:
        if (aid, page) in pb_obs_ids:
            tags.append("observable")
        elif (aid, page) in pb_pages_ids:
            tags.append("page_reviewed")
    return "+".join(tags)


def _build_changed_decision_log(
    pb_records: list[dict],
    pub_records: list[dict],
    result: dict[str, Any],
    lockfile: dict,
) -> list[dict[str, Any]]:
    """One row per (candidate, corpus, id) where a candidate flipped
    the verdict vs. baseline.
    """
    log: list[dict[str, Any]] = []
    pb_hist = parsebench_doc_historical(lockfile)
    pb_conf = parsebench_doc_reviewer_confirmed(lockfile)
    pb_pages_ids = {(r["asset"], r["page"]) for r in parsebench_page_eligibility(lockfile)}
    pb_obs_ids = {(r["asset"], r["page"]) for r in parsebench_observable_eligibility(lockfile)}

    # ParseBench document flips
    for name, _ in RULES:
        if name == "baseline":
            continue
        for aid in sorted(result["pb_rule_verdicts"][name]):
            b = result["pb_rule_verdicts"]["baseline"][aid]
            c = result["pb_rule_verdicts"][name][aid]
            if b == c:
                continue
            elig = _pb_eligibility_label(pb_hist, pb_conf, pb_pages_ids, pb_obs_ids, aid, None)
            log.append({
                "candidate": name,
                "corpus": "parsebench",
                "scope": "document",
                "id": aid,
                "baseline": bool(b),
                "candidate_verdict": bool(c),
                "flip": "silenced" if b and not c else "raised",
                "candidate_reason": _CANDIDATE_REASONS.get(name, ""),
                "ground_truth_eligibility": elig,
                "exclusion_reason": _asset_exclusion_reason(lockfile, aid) if not elig else "",
                "affects_document_verdict": True,
                "page_noise_only": False,
                "baseline_signals": {},
                "candidate_signals": {},
            })
    # ParseBench page flips
    for name, _ in RULES:
        if name == "baseline":
            continue
        for (aid, page), c in result["pb_rule_page_verdicts"][name].items():
            b = result["pb_rule_page_verdicts"]["baseline"][(aid, page)]
            if b == c:
                continue
            page_analyses = next(r["page_analyses"] for r in pb_records if r["id"] == aid)
            a: dict[str, Any] = next((x for x in page_analyses if x.get("page") == page), {})
            elig = _pb_eligibility_label(pb_hist, pb_conf, pb_pages_ids, pb_obs_ids, aid, page)
            # A page flip that does NOT change the document verdict is
            # "page noise only".
            doc_b = result["pb_rule_verdicts"]["baseline"][aid]
            doc_c = result["pb_rule_verdicts"][name][aid]
            log.append({
                "candidate": name,
                "corpus": "parsebench",
                "scope": "page",
                "id": f"{aid}#{page}",
                "baseline": bool(b),
                "candidate_verdict": bool(c),
                "flip": "silenced" if b and not c else "raised",
                "candidate_reason": _CANDIDATE_REASONS.get(name, ""),
                "ground_truth_eligibility": elig,
                "exclusion_reason": "" if elig else _asset_exclusion_reason(lockfile, aid),
                "affects_document_verdict": doc_b != doc_c,
                "page_noise_only": doc_b == doc_c,
                "baseline_signals": _summarise_signals(a),
                "candidate_signals": _summarise_signals(a),
            })
    # Public document flips
    labels_doc = json.load(_PUBLIC_LABELS.open("r", encoding="utf-8"))
    pub_hist = public_doc_historical(labels_doc.get("labels", {}))
    for name, _ in RULES:
        if name == "baseline":
            continue
        for pid in sorted(result["pub_rule_verdicts"][name]):
            b = result["pub_rule_verdicts"]["baseline"][pid]
            c = result["pub_rule_verdicts"][name][pid]
            if b == c:
                continue
            elig = "public_historical" if pid in pub_hist else ""
            log.append({
                "candidate": name,
                "corpus": "public_frozen",
                "scope": "document",
                "id": pid,
                "baseline": bool(b),
                "candidate_verdict": bool(c),
                "flip": "silenced" if b and not c else "raised",
                "candidate_reason": _CANDIDATE_REASONS.get(name, ""),
                "ground_truth_eligibility": elig,
                "exclusion_reason": "" if elig else "public_label_unattested",
                "affects_document_verdict": True,
                "page_noise_only": False,
                "baseline_signals": {},
                "candidate_signals": {},
            })
    return log


# ── Main ────────────────────────────────────────────────────────────────


def _run(output_json: Path, *, verify_cache_only: bool = False, workdir: Path | None = None) -> int:
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        lockfile = json.load(f)
    revision = (lockfile["dataset_source"] or {}).get("dataset_revision") or ""
    cache_dir = _parsebench_cache_dir(revision)
    if not cache_dir.exists():
        print(f"ParseBench cache missing at {cache_dir}", file=sys.stderr)
        return 30

    _verify_cache(lockfile["assets"], cache_dir)
    if verify_cache_only:
        print(f"ParseBench cache verified at {cache_dir}")
        return 0

    frozen, labels = _load_public_frozen()
    if frozen.get("harness_version") != _EXPECTED_PUBLIC_HARNESS_VERSION:
        print(
            "frozen public-corpus artifact has an unexpected harness_version: "
            f"got {frozen.get('harness_version')!r}, expected "
            f"{_EXPECTED_PUBLIC_HARNESS_VERSION!r}",
            file=sys.stderr,
        )
        return 31
    if frozen.get("commit") != _EXPECTED_PUBLIC_COMMIT:
        print(
            "frozen public-corpus artifact has an unexpected commit: "
            f"got {frozen.get('commit')!r}, expected "
            f"{_EXPECTED_PUBLIC_COMMIT!r}",
            file=sys.stderr,
        )
        return 32

    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="parsebench_recal_"))

    pb_records = _parsebench_records(lockfile, cache_dir, workdir)
    pub_records = _public_records(frozen, labels)

    result = _all_slices(pb_records, pub_records, lockfile)
    log = _build_changed_decision_log(pb_records, pub_records, result, lockfile)

    output = {
        "harness_version": "parsebench_recalibration.py@2026-07-19",
        "run_started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit_under_evaluation": _current_commit(),
        "public_frozen_source": _PUBLIC_FROZEN_RESULTS.name,
        "public_labels_source": _PUBLIC_LABELS.name,
        "lockfile_source": _LOCKFILE.name,
        "rules": {
            "baseline": "HTR OR (|signals| >= 2), with gap gate gap_rel>=0.15 AND gap_size>=60",
            "C3": "same warn logic as baseline but raise gap gate to gap_rel>=0.30 AND gap_size>=60",
            "C4": "warn iff HTR AND (YMT OR SF), with baseline gap gate",
            "C3+C4": "warn iff HTR AND (YMT OR SF), with C3 gap gate",
        },
        "corpus_counts": {
            "parsebench": {
                "assets_total": len(pb_records),
                "doc_historical_eligible": len(result["eligibility"]["parsebench_doc_historical_ids"]),
                "doc_reviewer_confirmed_eligible": len(result["eligibility"]["parsebench_doc_reviewer_confirmed_ids"]),
                "page_eligible": len(result["eligibility"]["parsebench_page_ids"]),
                "observable_eligible": len(result["eligibility"]["parsebench_observable_ids"]),
            },
            "public_frozen": {
                "results_total": len(pub_records),
                "doc_historical_eligible": len(result["eligibility"]["public_doc_historical_ids"]),
            },
        },
        "eligibility_ids": result["eligibility"],
        "metrics": {
            name: {
                "rule": name,
                "parsebench": result["slices"][name]["parsebench"],
                "public_frozen": result["slices"][name]["public_frozen"],
                "combined": result["slices"][name]["combined"],
            }
            for name, _ in RULES
        },
        "per_document_parsebench": [
            {
                "asset": rec["id"],
                "expected_label": rec["expected_label"],
                "defect_kind": rec["defect_kind"],
                "baseline_warn_doc": result["pb_rule_verdicts"]["baseline"][rec["id"]],
                "C3_warn_doc": result["pb_rule_verdicts"]["C3"][rec["id"]],
                "C4_warn_doc": result["pb_rule_verdicts"]["C4"][rec["id"]],
                "C3+C4_warn_doc": result["pb_rule_verdicts"]["C3+C4"][rec["id"]],
                "compile_elapsed_s": rec["compile_elapsed_s"],
            }
            for rec in pb_records
        ],
        "per_document_public_frozen": [
            {
                "id": rec["id"],
                "expected_positive": rec["expected_positive"],
                "baseline_warn_doc": result["pub_rule_verdicts"]["baseline"][rec["id"]],
                "C3_warn_doc": result["pub_rule_verdicts"]["C3"][rec["id"]],
                "C4_warn_doc": result["pub_rule_verdicts"]["C4"][rec["id"]],
                "C3+C4_warn_doc": result["pub_rule_verdicts"]["C3+C4"][rec["id"]],
            }
            for rec in pub_records
        ],
        "changed_decisions": log,
    }
    output_json.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"wrote {output_json}")
    return 0


def _current_commit() -> str:
    try:
        return subprocess.check_output(  # nosec B603 B607 - reading local git head, no user input
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT, text=True,
        ).strip()
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=False,
                    default=_REPO_ROOT / "benchmarks" / "PARSEBENCH_RECALIBRATION_2026-07-19.json")
    ap.add_argument("--verify-cache-only", action="store_true",
                    help="Verify the ParseBench cache and exit.")
    ap.add_argument("--workdir", type=Path, default=None,
                    help="Compile output directory (defaults to a temp dir).")
    args = ap.parse_args()
    return _run(args.output, verify_cache_only=args.verify_cache_only, workdir=args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
