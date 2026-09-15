"""Prove-one acceptance test for the selected Federal Register document (B1a-4).

Checklist matches the human's spec:

1. Manifest schema version constant.
2. Distribution provenance completeness (source, pinned date range,
   population_publication_date_lte is a literal ISO date).
3. FR-API identity round-trip: document_number + publication_date +
   volume + type recorded and consistent.
4. GovInfo cross-check identity: package_id + granule_id derived from
   pdf_url, matches the recorded values.
5. Local manifest SHA-256s match cached bytes (PDF, XML, API JSON).
6. Volume >= 60 (born-digital FR PDF era).
7. Type == 'Rule'.
8. Adapter capability declaration:
   - supports_clean_native_fpr is True
   - supports_textual_gt is False with an explicit §2.4-caveat-3 reason
   - other capabilities False
9. Adapter ingest_source returns the PDF (media_type, path, sha256).
   XML surfaces on provenance under `adjudication_support`.
10. Adapter ingest_ground_truth kind == 'fpr_baseline', payload
    contains NO oracle_text / expected_text / expected_prose / word_set,
    contract semantics are recorded.
11. Idempotent reacquire (mtime + hashes unchanged).
12. Ledger contains an entry for the selected document with
    b1_pilot_eligible=False, held_out_v1_eligible=False.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from benchmarks.eval_v1.acquisition.federal_register_api import (
    MANIFEST_SCHEMA_VERSION,
    govinfo_ids_from_pdf_url,
)
from benchmarks.eval_v1.adapters.federal_register_v1 import (
    EXTRACTION_RULES_VERSION,
    GT_KIND,
    FederalRegisterAsset,
    FederalRegisterV1Adapter,
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _check(
    checks: list[CheckResult], name: str, ok: bool, detail: str = ""
) -> None:
    checks.append(CheckResult(name=name, ok=bool(ok), detail=detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _load_asset(doc_dir: Path) -> FederalRegisterAsset:
    manifest = json.loads((doc_dir / "manifest.json").read_text())
    doc_number = manifest["document_number"]
    return FederalRegisterAsset(
        document_number=doc_number,
        publication_date=manifest["publication_date"],
        package_id=manifest["govinfo"]["package_id"],
        granule_id=manifest["govinfo"]["granule_id"],
        pdf_path=doc_dir / manifest["pdf"]["path"],
        xml_path=doc_dir / manifest["xml"]["path"] if manifest.get("xml", {}).get("path") else None,
        api_json_path=doc_dir / manifest["api_json"]["path"],
        manifest_path=doc_dir / "manifest.json",
    )


def run(doc_dir: Path, ledger_path: Path, report_path: Path) -> bool:
    checks: list[CheckResult] = []
    asset = _load_asset(doc_dir)
    manifest = json.loads(asset.manifest_path.read_text())
    print(f"[accept] target: document_number={asset.document_number}  dir: {doc_dir}")

    # 1. Manifest schema version
    print("[accept] --- 1. Manifest schema version ---")
    _check(
        checks,
        "manifest.schema_version_matches_constant",
        manifest["schema_version"] == MANIFEST_SCHEMA_VERSION,
    )

    # 2. Distribution provenance completeness
    print("[accept] --- 2. Distribution provenance ---")
    dist = manifest["distribution"]
    _check(checks, "manifest.distribution.source_is_fr_api", dist.get("source") == "federal_register_api_v1")
    _check(
        checks,
        "manifest.distribution.population_publication_date_lte_is_iso",
        _is_iso_date(dist.get("population_publication_date_lte")),
        f"lte={dist.get('population_publication_date_lte')}",
    )
    _check(
        checks,
        "manifest.distribution.population_publication_date_gte_is_iso",
        _is_iso_date(dist.get("population_publication_date_gte")),
        f"gte={dist.get('population_publication_date_gte')}",
    )
    # And gte <= lte
    if _is_iso_date(dist.get("population_publication_date_gte")) and _is_iso_date(
        dist.get("population_publication_date_lte")
    ):
        _check(
            checks,
            "manifest.distribution.date_range_gte_le_lte",
            date.fromisoformat(dist["population_publication_date_gte"])
            <= date.fromisoformat(dist["population_publication_date_lte"]),
        )

    # 3. FR-API identity
    print("[accept] --- 3. FR-API identity ---")
    _check(checks, "manifest.document_number_present", bool(manifest.get("document_number")))
    _check(checks, "manifest.publication_date_iso", _is_iso_date(manifest.get("publication_date")))
    _check(checks, "manifest.volume_present_and_int", isinstance(manifest.get("volume"), int))
    _check(checks, "manifest.type_present", isinstance(manifest.get("type"), str) and manifest["type"])

    # 4. GovInfo cross-check
    print("[accept] --- 4. GovInfo identity cross-check ---")
    pkg_manifest = manifest["govinfo"]["package_id"]
    gran_manifest = manifest["govinfo"]["granule_id"]
    pkg_from_url, gran_from_url = govinfo_ids_from_pdf_url(manifest["pdf"]["canonical_url"])
    _check(
        checks,
        "govinfo.package_id_matches_pdf_url_parse",
        pkg_manifest == pkg_from_url,
        f"manifest={pkg_manifest} url={pkg_from_url}",
    )
    _check(
        checks,
        "govinfo.granule_id_matches_pdf_url_parse",
        gran_manifest == gran_from_url,
        f"manifest={gran_manifest} url={gran_from_url}",
    )
    _check(
        checks,
        "govinfo.granule_id_equals_document_number",
        gran_manifest == manifest["document_number"],
        f"granule_id={gran_manifest} document_number={manifest['document_number']}",
    )
    _check(
        checks,
        "govinfo.package_id_date_matches_publication_date",
        pkg_manifest == f"FR-{manifest['publication_date']}",
        f"package_id={pkg_manifest} publication_date={manifest['publication_date']}",
    )

    # 5. Hash consistency
    print("[accept] --- 5. Local manifest hashes match cached bytes ---")
    pdf_bytes = asset.pdf_path.read_bytes()
    _check(
        checks,
        "manifest.pdf_sha256_matches",
        _sha256(pdf_bytes) == manifest["pdf"]["sha256"],
    )
    if asset.xml_path is not None:
        xml_bytes = asset.xml_path.read_bytes()
        _check(
            checks,
            "manifest.xml_sha256_matches",
            _sha256(xml_bytes) == manifest["xml"]["sha256"],
        )
    api_bytes = asset.api_json_path.read_bytes()
    _check(
        checks,
        "manifest.api_json_sha256_matches",
        _sha256(api_bytes) == manifest["api_json"]["sha256"],
    )

    # 6. Volume gate
    print("[accept] --- 6. Volume >= 60 (born-digital era) ---")
    _check(
        checks,
        "filter.volume_ge_60",
        (manifest.get("volume") or 0) >= 60,
        f"volume={manifest.get('volume')}",
    )

    # 7. Type
    print("[accept] --- 7. Type == 'Rule' ---")
    _check(checks, "filter.type_is_rule", manifest.get("type") == "Rule")

    # 8. Capability declaration
    print("[accept] --- 8. Capability declaration ---")
    adapter = FederalRegisterV1Adapter({asset.document_number: asset})
    caps = adapter.capabilities()
    _check(checks, "capabilities.supports_clean_native_fpr", caps.supports_clean_native_fpr is True)
    _check(checks, "capabilities.supports_textual_gt_is_false", caps.supports_textual_gt is False)
    _check(
        checks,
        "capabilities.textual_gt_reason_cites_caveat_3",
        "caveat 3" in (caps.not_applicable_reasons.get("textual_gt") or ""),
    )
    for f in ("supports_layout_gt", "supports_clause_span_gt", "supports_downstream_qa_gt"):
        _check(checks, f"capabilities.{f}_is_false", getattr(caps, f) is False)

    # 9. Adapter ingest_source
    print("[accept] --- 9. Adapter ingest_source (PDF is parser source) ---")
    si = adapter.ingest_source(asset.document_number)
    _check(checks, "adapter.ingest_source_media_type_pdf", si.media_type == "application/pdf")
    _check(checks, "adapter.ingest_source_sha256_matches_pdf", si.sha256 == manifest["pdf"]["sha256"])
    _check(checks, "adapter.ingest_source_path_is_pdf", si.path == asset.pdf_path)
    _check(
        checks,
        "adapter.ingest_source_source_role_records_authoritative",
        si.provenance.get("source_role") == "parser_input_authoritative_record",
    )
    _check(
        checks,
        "adapter.ingest_source_records_pinned_date_lte",
        si.provenance.get("population_publication_date_lte")
        == dist.get("population_publication_date_lte"),
    )
    # XML present on provenance under adjudication_support
    adj = si.provenance.get("adjudication_support") or {}
    _check(
        checks,
        "adapter.ingest_source_adjudication_support_records_xml",
        adj.get("xml_sha256") == manifest["xml"]["sha256"]
        and adj.get("role") == "g2_adjudication_support_only",
    )

    # 10. Adapter ingest_ground_truth — no G1 fields, contract recorded
    print("[accept] --- 10. Adapter ingest_ground_truth (fpr_baseline, no G1) ---")
    gt = adapter.ingest_ground_truth(asset.document_number)
    if gt is None:
        raise RuntimeError(
            f"adapter returned no ground truth for {asset.document_number}"
        )
    _check(checks, "gt.kind_is_fpr_baseline", gt.kind == GT_KIND)
    _check(
        checks,
        "gt.provenance_extraction_rules_version",
        gt.provenance.get("extraction_rules_version") == EXTRACTION_RULES_VERSION,
    )
    for forbidden in ("oracle_text", "expected_text", "expected_prose", "word_set", "tokens"):
        _check(
            checks,
            f"gt.data_has_no_{forbidden}_field",
            forbidden not in gt.data,
            f"forbidden field {forbidden!r} would signal G1 semantics",
        )
    contract = gt.data.get("contract") or {}
    _check(
        checks,
        "gt.contract_meaning_records_g2_adjudication_semantics",
        "human adjudication" in (contract.get("meaning") or "").lower(),
    )
    _check(
        checks,
        "gt.contract_native_authored_pdf_definition_recorded",
        "born-digital" in (contract.get("native_authored_pdf_definition") or ""),
    )
    _check(
        checks,
        "gt.provenance_records_pinned_date_lte",
        gt.provenance.get("population_publication_date_lte")
        == dist.get("population_publication_date_lte"),
    )

    # 11. Idempotent reacquire
    print("[accept] --- 11. Idempotent reacquire ---")
    mtime_before = asset.manifest_path.stat().st_mtime_ns
    adapter2 = FederalRegisterV1Adapter({asset.document_number: asset})
    si2 = adapter2.ingest_source(asset.document_number)
    gt2 = adapter2.ingest_ground_truth(asset.document_number)
    if gt2 is None:
        raise RuntimeError("reacquire produced no ground truth")
    _check(
        checks,
        "reacquire.manifest_mtime_unchanged",
        asset.manifest_path.stat().st_mtime_ns == mtime_before,
    )
    _check(checks, "reacquire.pdf_sha256_stable", si2.sha256 == si.sha256)
    _check(
        checks,
        "reacquire.gt_xml_sha256_stable",
        gt2.provenance.get("xml_sha256") == gt.provenance.get("xml_sha256"),
    )

    # 12. Ledger
    print("[accept] --- 12. Exclusion ledger entry ---")
    ledger_ok = False
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if (
                entry.get("document_number") == asset.document_number
                and entry.get("evaluation_eligibility") == "development_only"
                and entry.get("held_out_v1_eligible") is False
                and entry.get("b1_pilot_eligible") is False
            ):
                ledger_ok = True
                break
    _check(checks, "ledger.selected_document_recorded", ledger_ok)

    all_ok = all(c.ok for c in checks)
    print(f"\n{'=' * 60}")
    print(f"FEDERAL REGISTER PROVE-ONE: {'PASS' if all_ok else 'FAIL'}")
    print(f"  {sum(c.ok for c in checks)}/{len(checks)} checks passed")
    print("=" * 60)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "verdict": "PASS" if all_ok else "FAIL",
                "document_number": asset.document_number,
                "publication_date": asset.publication_date,
                "package_id": asset.package_id,
                "granule_id": asset.granule_id,
                "doc_dir": str(doc_dir),
                "extraction_rules_version": EXTRACTION_RULES_VERSION,
                "verified_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
                "checks_passed": sum(c.ok for c in checks),
                "checks_total": len(checks),
                "results": [
                    {"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(f"[accept] report -> {report_path}")
    return all_ok


def _is_iso_date(s: object) -> bool:
    if not isinstance(s, str):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--doc-dir", type=Path, required=True)
    p.add_argument(
        "--ledger",
        type=Path,
        default=Path("docs/evaluation/FEDERAL_REGISTER_EXCLUSION_LEDGER.jsonl"),
    )
    p.add_argument(
        "--report",
        type=Path,
        default=Path("docs/evaluation/FEDERAL_REGISTER_PROVE_ONE_ACCEPTANCE.json"),
    )
    args = p.parse_args(argv)
    ok = run(args.doc_dir, args.ledger, args.report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
