"""Prove-one acceptance test for the selected DocLayNet page (B1a-3).

Runs the acceptance checklist per B1a discipline (mirrors the PMC-OA
25-check pattern, with the DocLayNet-specific coordinate-conversion
check the human called out):

1. HF dataset revision resolves and matches manifest.
2. Manifest schema version matches acquisition-side constant.
3. Distribution provenance carries dataset_id + commit_sha + shard_key.
4. Local manifest SHA-256s match cached bytes (PNG, PDF, annotations).
5. Adapter ingest_source returns expected PNG SHA-256 + media_type.
6. Adapter ingest_ground_truth non-null; kind == "bbox_layout";
   extraction_rules_version + dataset_commit_sha on provenance.
7. Coordinate-space header is present + consistent with distribution.
8. Ground-truth data payload contains bboxes + categories only —
   NO expected_reading_order (§2.4 caveat 4) and NO table_cell_structure
   (§2.4 caveat 5).
9. At least one Table region + at least one non-Text non-Table
   structural region (enforcement of eligibility surviving to oracle).
10. **Coordinate-conversion round-trip**: convert every bbox from
    PNG-pixel space to native PDF-point space via the canonical
    helper; assert (a) scale factors are strictly positive, (b) every
    converted bbox stays inside [0, original_width] × [0, original_height]
    within a 1-pixel tolerance, (c) conversion is reversible to within
    numerical tolerance. This is the silent-tank-recall failure mode
    the recon flagged.
11. Idempotent reacquisition: rerunning the selector does not mutate
    hashes or manifest mtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.eval_v1.acquisition.doclaynet_hf import (
    MANIFEST_SCHEMA_VERSION,
    resolve_dataset_ref,
)
from benchmarks.eval_v1.adapters.doclaynet_v1 import (
    EXTRACTION_RULES_VERSION,
    GT_KIND,
    DocLayNetAsset,
    DocLayNetV1Adapter,
    convert_bbox_to_pdf_points,
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


def _load_asset(page_dir: Path) -> DocLayNetAsset:
    manifest = json.loads((page_dir / "manifest.json").read_text())
    pdf_meta = manifest["pdf"]
    pdf_path = page_dir / pdf_meta["path"] if pdf_meta.get("path") else None
    return DocLayNetAsset(
        page_hash=manifest["page_hash"],
        manifest_path=page_dir / "manifest.json",
        annotations_path=page_dir / manifest["annotations_file"]["path"],
        png_path=page_dir / manifest["png"]["path"],
        pdf_path=pdf_path,
    )


def run(page_dir: Path, report_path: Path) -> bool:
    checks: list[CheckResult] = []
    asset = _load_asset(page_dir)
    manifest = json.loads(asset.manifest_path.read_text())
    print(f"[accept] target: page_hash={asset.page_hash}  dir: {page_dir}")

    # 1. Resolver
    print("[accept] --- 1. HF dataset revision ---")
    ref = resolve_dataset_ref()
    _check(
        checks,
        "resolver.dataset_id_matches",
        ref.dataset_id == manifest["distribution"]["dataset_id"],
        f"resolver={ref.dataset_id}",
    )
    _check(
        checks,
        "resolver.commit_sha_matches_manifest",
        ref.sha == manifest["distribution"]["dataset_commit_sha"],
        f"resolver_sha={ref.sha[:12]}..., manifest_sha={manifest['distribution']['dataset_commit_sha'][:12]}...",
    )

    # 2. Manifest schema
    print("[accept] --- 2. Manifest schema version ---")
    _check(
        checks,
        "manifest.schema_version_matches_constant",
        manifest["schema_version"] == MANIFEST_SCHEMA_VERSION,
    )

    # 3. Distribution provenance
    print("[accept] --- 3. Distribution provenance completeness ---")
    dist = manifest["distribution"]
    _check(checks, "manifest.distribution.dataset_id", bool(dist.get("dataset_id")))
    _check(
        checks,
        "manifest.distribution.dataset_commit_sha",
        bool(dist.get("dataset_commit_sha")) and len(dist["dataset_commit_sha"]) == 40,
    )
    _check(checks, "manifest.distribution.shard_key", bool(dist.get("shard_key")))
    _check(
        checks,
        "manifest.distribution.split_is_train",
        dist.get("split") == "train",
        f"split={dist.get('split')}",
    )

    # 4. Hash consistency
    print("[accept] --- 4. Local manifest hashes match cached bytes ---")
    png_bytes = asset.png_path.read_bytes()
    _check(
        checks,
        "manifest.png_sha256_matches",
        _sha256(png_bytes) == manifest["png"]["sha256"],
    )
    ann_bytes = asset.annotations_path.read_bytes()
    _check(
        checks,
        "manifest.annotations_sha256_matches",
        _sha256(ann_bytes) == manifest["annotations_file"]["sha256"],
    )
    if manifest["pdf"].get("path"):
        pdf_bytes = asset.pdf_path.read_bytes() if asset.pdf_path else b""
        _check(
            checks,
            "manifest.pdf_sha256_matches",
            _sha256(pdf_bytes) == manifest["pdf"]["sha256"],
        )
    else:
        _check(
            checks,
            "manifest.pdf_absent_recorded_as_such",
            manifest["pdf"].get("present_in_distribution") is False,
        )

    # 5. Adapter ingest_source
    print("[accept] --- 5. Adapter ingest_source ---")
    adapter = DocLayNetV1Adapter({asset.page_hash: asset})
    si = adapter.ingest_source(asset.page_hash)
    _check(
        checks,
        "adapter.ingest_source_sha256",
        si.sha256 == manifest["png"]["sha256"],
    )
    _check(
        checks,
        "adapter.ingest_source_media_type_png",
        si.media_type == "image/png",
    )
    _check(
        checks,
        "adapter.ingest_source_provenance_dataset_commit_sha",
        si.provenance.get("dataset_commit_sha") == ref.sha,
    )

    # 6. Adapter ingest_ground_truth
    print("[accept] --- 6. Adapter ingest_ground_truth ---")
    gt = adapter.ingest_ground_truth(asset.page_hash)
    if gt is None:
        raise RuntimeError(
            f"adapter returned no ground truth for page_hash={asset.page_hash}"
        )
    _check(checks, "adapter.gt_kind_bbox_layout", gt.kind == GT_KIND)
    _check(
        checks,
        "adapter.gt_provenance_extraction_rules_version",
        gt.provenance.get("extraction_rules_version") == EXTRACTION_RULES_VERSION,
    )
    _check(
        checks,
        "adapter.gt_provenance_dataset_commit_sha",
        gt.provenance.get("dataset_commit_sha") == ref.sha,
    )

    # 7. Coordinate-space header
    print("[accept] --- 7. Coordinate-space header ---")
    coord = gt.data["coordinate_space"]
    _check(checks, "gt.coord_space_kind_png_pixels", coord["kind"] == "png_pixels")
    _check(checks, "gt.coord_space_coco_width_positive", coord["coco_width"] > 0)
    _check(checks, "gt.coord_space_coco_height_positive", coord["coco_height"] > 0)
    _check(checks, "gt.coord_space_original_width_positive", coord["original_width"] > 0)
    _check(checks, "gt.coord_space_original_height_positive", coord["original_height"] > 0)

    # 8. Primitive-oracle purity: no derived fields leaked
    print("[accept] --- 8. Primitive-oracle purity ---")
    _check(
        checks,
        "gt.no_expected_reading_order_field",
        "expected_reading_order" not in gt.data,
    )
    _check(
        checks,
        "gt.no_table_cell_structure_field",
        "table_cell_structure" not in gt.data,
    )

    # 9. Eligibility survives to oracle
    print("[accept] --- 9. Eligibility survives to oracle ---")
    cats = [a["category"] for a in gt.data["annotations"]]
    has_table = "Table" in cats
    non_text_non_table = [c for c in cats if c not in ("Text", "Table")]
    _check(checks, "gt.contains_at_least_one_Table_region", has_table,
           f"table_count={cats.count('Table')}")
    _check(
        checks,
        "gt.contains_at_least_one_non_text_structural_region",
        len(non_text_non_table) > 0,
        f"non_text_non_table_regions={len(non_text_non_table)}",
    )

    # 10. Coordinate-conversion round-trip — the silent-tank-recall check
    print("[accept] --- 10. Coordinate-space conversion round-trip ---")
    cw, ch = coord["coco_width"], coord["coco_height"]
    ow, oh = coord["original_width"], coord["original_height"]
    sx = ow / cw
    sy = oh / ch
    _check(checks, "convert.scale_x_positive", sx > 0, f"sx={sx:.6f}")
    _check(checks, "convert.scale_y_positive", sy > 0, f"sy={sy:.6f}")

    all_inside = True
    max_reverse_error = 0.0
    for i, ann in enumerate(gt.data["annotations"]):
        bbox_png = ann["bbox_png"]
        conv = convert_bbox_to_pdf_points(bbox_png, cw, ch, ow, oh)
        x, y, w, h = conv
        tol = 1.0  # 1-pixel-equivalent tolerance
        if not (
            -tol <= x <= ow + tol
            and -tol <= y <= oh + tol
            and x + w <= ow + tol
            and y + h <= oh + tol
        ):
            all_inside = False
            print(f"    OUT-OF-PAGE bbox {i}: {conv} in a {ow}×{oh} page")
        # reverse: convert PDF-point -> PNG using the inverse map, compare
        rx, ry = x / sx, y / sy
        rw, rh = w / sx, h / sy
        err = max(
            abs(rx - bbox_png[0]),
            abs(ry - bbox_png[1]),
            abs(rw - bbox_png[2]),
            abs(rh - bbox_png[3]),
        )
        if err > max_reverse_error:
            max_reverse_error = err
    _check(
        checks,
        "convert.every_bbox_inside_page_within_1px",
        all_inside,
        f"tested {len(gt.data['annotations'])} bboxes",
    )
    _check(
        checks,
        "convert.reversible_within_numerical_tolerance",
        max_reverse_error < 1e-6,
        f"max_reverse_error={max_reverse_error:.2e}",
    )

    # 11. Idempotent reacquisition (re-hash cached files matches manifest)
    print("[accept] --- 11. Idempotent cache re-verify ---")
    mtime_before = asset.manifest_path.stat().st_mtime_ns
    # Re-instantiate adapter, re-run ingest_source/ingest_ground_truth —
    # this exercises the same cache-verification path acquire_page uses.
    adapter2 = DocLayNetV1Adapter({asset.page_hash: asset})
    si2 = adapter2.ingest_source(asset.page_hash)
    gt2 = adapter2.ingest_ground_truth(asset.page_hash)
    if gt2 is None:
        raise RuntimeError("reacquire produced no ground truth")
    _check(
        checks,
        "reacquire.manifest_mtime_unchanged",
        asset.manifest_path.stat().st_mtime_ns == mtime_before,
    )
    _check(checks, "reacquire.source_sha256_stable", si2.sha256 == si.sha256)
    _check(
        checks,
        "reacquire.gt_annotations_sha256_stable",
        gt2.provenance["annotations_sha256"] == gt.provenance["annotations_sha256"],
    )

    all_ok = all(c.ok for c in checks)
    print(f"\n{'=' * 60}")
    print(f"DocLayNet PROVE-ONE: {'PASS' if all_ok else 'FAIL'}")
    print(f"  {sum(c.ok for c in checks)}/{len(checks)} checks passed")
    print("=" * 60)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "verdict": "PASS" if all_ok else "FAIL",
                "page_hash": asset.page_hash,
                "page_dir": str(page_dir),
                "extraction_rules_version": EXTRACTION_RULES_VERSION,
                "dataset_id": manifest["distribution"]["dataset_id"],
                "dataset_commit_sha": manifest["distribution"]["dataset_commit_sha"],
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--page-dir", type=Path, required=True)
    p.add_argument(
        "--report",
        type=Path,
        default=Path("docs/evaluation/DOCLAYNET_PROVE_ONE_ACCEPTANCE.json"),
    )
    args = p.parse_args(argv)
    ok = run(args.page_dir, args.report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
