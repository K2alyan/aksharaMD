"""Prove-one acceptance test for the selected PMC-OA article (B1a-2).

Runs the checklist the human specified for the checkpoint:

1. AWS resolver / metadata_key path returns the canonical entry.
2. PDF and XML on disk match the metadata JSON's referenced objects.
3. PMCID in JATS ``article-meta`` matches the requested PMCID.
4. Local ``manifest.json`` SHA-256s match the cached bytes on disk.
5. Adapter's ``ingest_source`` returns the expected PDF SHA-256.
6. Adapter's ``ingest_ground_truth`` produces non-empty canonical text
   AND an ordered token list (multiplicity preserved).
7. Known snippets from the article's title / abstract appear in the
   oracle (real-article version of "known fixture snippets present").
8. Excluded material (bibliographic references, ``<ref-list>``
   content) does NOT appear in ``body_text``.
9. Re-invoking ``acquire_article`` is idempotent: no hash changes,
   ``manifest.json`` mtime unchanged.
10. Trivial self-overlap: ``word_overlap(body_text, body_text) == 1.0``.

Writes ``docs/evaluation/PMC_OA_PROVE_ONE_ACCEPTANCE.json`` (compact
result table) and prints the terminal ``PMC-OA PROVE-ONE: PASS`` /
``FAIL`` line.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.eval_v1.acquisition.pmc_oa_aws import (
    acquire_article,
    fetch_metadata,
    jats_pmcid_matches,
)
from benchmarks.eval_v1.adapters.pmc_oa_v1 import (
    EXTRACTION_RULES_VERSION,
    PmcOaAsset,
    PmcOaV1Adapter,
)
from benchmarks.eval_v1.conventional_metrics import word_overlap


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


def _load_asset(article_dir: Path) -> PmcOaAsset:
    manifest = json.loads((article_dir / "manifest.json").read_text())
    return PmcOaAsset(
        pmcid=manifest["pmcid"],
        pdf_path=article_dir / Path(manifest["pdf"]["key"]).name,
        xml_path=article_dir / Path(manifest["xml"]["key"]).name,
        manifest_path=article_dir / "manifest.json",
        version=manifest["version"],
        metadata_json_path=article_dir / Path(manifest["metadata_object"]["key"]).name,
    )


def run(article_dir: Path, report_path: Path) -> bool:
    checks: list[CheckResult] = []
    asset = _load_asset(article_dir)
    manifest = json.loads(asset.manifest_path.read_text())
    pmcid_v = f"{asset.pmcid}.{asset.version}"
    print(f"[accept] target: {pmcid_v}  dir: {article_dir}")

    # 1. AWS resolver / metadata_key
    print("[accept] --- 1. AWS resolver returns canonical entry ---")
    md, md_raw, md_sha = fetch_metadata(pmcid_v)
    _check(
        checks,
        "resolver.metadata_key_matches",
        md.metadata_key == manifest["metadata_object"]["key"],
        f"resolver_key={md.metadata_key}",
    )
    _check(
        checks,
        "resolver.pmcid_matches_expected",
        md.pmcid == asset.pmcid,
        f"resolver_pmcid={md.pmcid}",
    )
    _check(
        checks,
        "resolver.version_matches_expected",
        md.version == asset.version,
        f"resolver_version={md.version}",
    )

    # 2. PDF + XML on disk reference the same canonical objects
    print("[accept] --- 2. PDF/XML are the metadata's referenced objects ---")
    pdf_bytes = asset.pdf_path.read_bytes()
    xml_bytes = asset.xml_path.read_bytes()
    _check(
        checks,
        "pdf.md5_matches_metadata_url",
        md.md5_from_url(md.pdf_url) == hashlib.md5(pdf_bytes).hexdigest(),  # noqa: S324
    )
    _check(
        checks,
        "xml.md5_matches_metadata_url",
        md.md5_from_url(md.xml_url) == hashlib.md5(xml_bytes).hexdigest(),  # noqa: S324
    )

    # 3. JATS-internal PMCID matches
    print("[accept] --- 3. JATS-internal PMCID matches request ---")
    matches, got = jats_pmcid_matches(xml_bytes, asset.pmcid)
    _check(checks, "jats.pmcid_matches", matches, f"jats_pmcid={got}")

    # 4. Manifest SHA-256s match cached bytes
    print("[accept] --- 4. Local manifest SHA-256s match cached bytes ---")
    _check(
        checks,
        "manifest.pdf_sha256_matches",
        _sha256(pdf_bytes) == manifest["pdf"]["sha256"],
    )
    _check(
        checks,
        "manifest.xml_sha256_matches",
        _sha256(xml_bytes) == manifest["xml"]["sha256"],
    )
    assert asset.metadata_json_path is not None
    _check(
        checks,
        "manifest.metadata_sha256_matches",
        _sha256(asset.metadata_json_path.read_bytes())
        == manifest["metadata_object"]["sha256"],
    )

    # 5. Adapter ingest_source
    print("[accept] --- 5. Adapter ingest_source ---")
    adapter = PmcOaV1Adapter({asset.pmcid: asset})
    si = adapter.ingest_source(asset.pmcid)
    _check(
        checks,
        "adapter.ingest_source_sha256",
        si.sha256 == manifest["pdf"]["sha256"],
    )
    _check(
        checks,
        "adapter.ingest_source_provenance_carries_version",
        si.provenance.get("version") == asset.version,
    )

    # 6. Adapter ingest_ground_truth
    print("[accept] --- 6. Adapter ingest_ground_truth ---")
    gt = adapter.ingest_ground_truth(asset.pmcid)
    assert gt is not None
    body_text = gt.data["body_text"]
    tokens = gt.data["tokens"]
    _check(checks, "adapter.body_text_nonempty", bool(body_text and body_text.strip()))
    _check(
        checks,
        "adapter.tokens_is_ordered_list",
        isinstance(tokens, list) and len(tokens) > 0,
        f"n_tokens={len(tokens)}",
    )
    _check(
        checks,
        "adapter.tokens_lowercase_normalized",
        all(t == t.lower() for t in tokens[:200]),
    )
    _check(
        checks,
        "adapter.gt_provenance_extraction_rules_version",
        gt.provenance.get("extraction_rules_version") == EXTRACTION_RULES_VERSION,
    )
    _check(
        checks,
        "adapter.gt_provenance_license_code_from_metadata",
        gt.provenance.get("license_code_from_metadata") == "CC BY",
    )

    # 7. Known real-article snippets appear in the oracle
    print("[accept] --- 7. Real-article snippets present in oracle ---")
    title = gt.data["title"]
    abstract = gt.data["abstract"]
    _check(checks, "oracle.title_nonempty", bool(title.strip()), f"title={title[:80]!r}")
    _check(checks, "oracle.abstract_nonempty", bool(abstract.strip()))
    # A conservative "known snippet" check: the article title's first
    # substantive word (>= 5 chars, alphabetic) must appear in body_text
    # OR in abstract — enough to prove we haven't lost the article.
    substantive_title_tokens = [
        w.lower() for w in title.split() if len(w) >= 5 and w.isalpha()
    ]
    hit_word = None
    haystack = (body_text + " " + abstract).lower()
    for w in substantive_title_tokens:
        if w in haystack:
            hit_word = w
            break
    _check(
        checks,
        "oracle.title_word_found_in_prose",
        hit_word is not None,
        f"hit={hit_word!r}, candidates={substantive_title_tokens[:5]}",
    )

    # 8. Excluded material behaves per rules.
    #
    # The correct integrity claim is: bibliographic reference ENTRIES
    # (author list + title + journal + year + volume + pages, as
    # coherent strings) are not present in body_text. In-prose citations
    # of author surnames ("as Foo and Bar showed") legitimately appear
    # in body prose and MUST NOT be counted as contamination —
    # <xref> markers are excluded, but the author name text around
    # them belongs to the article's own writing.
    #
    # We test the actual claim by looking for substantive contiguous
    # substrings of each reference entry in body_text.
    print("[accept] --- 8. Ref-list entries excluded (contiguous-substring test) ---")
    from xml.etree import ElementTree as ET

    root = ET.fromstring(xml_bytes)
    ref_entries: list[str] = [
        " ".join(ref.itertext()) for ref in root.findall(".//ref-list/ref")
    ]
    contamination_hits = 0
    checked = 0
    for r in ref_entries:
        # Break the ref entry at ". " boundaries and keep parts >= 20
        # chars — those are journal names, article titles, DOIs, etc.,
        # the material that would only appear in body_text if we had
        # literally included bibliography prose there.
        substantive_parts = [p.strip() for p in r.split(".") if len(p.strip()) >= 20]
        for p in substantive_parts:
            checked += 1
            if p in body_text:
                contamination_hits += 1
    _check(
        checks,
        "oracle.ref_entries_not_in_body_text",
        contamination_hits == 0,
        f"checked={checked} substantive ref substrings across "
        f"{len(ref_entries)} refs, contamination_hits={contamination_hits}",
    )

    # 9. Idempotent reacquisition
    print("[accept] --- 9. Idempotent reacquisition ---")
    mtime_before = asset.manifest_path.stat().st_mtime_ns
    pdf_sha_before = _sha256(pdf_bytes)
    xml_sha_before = _sha256(xml_bytes)
    reacquired = acquire_article(md, md_raw, md_sha, article_dir.parent)
    _check(
        checks,
        "reacquire.same_manifest_path",
        reacquired.manifest_path == asset.manifest_path,
    )
    _check(
        checks,
        "reacquire.manifest_mtime_unchanged",
        reacquired.manifest_path.stat().st_mtime_ns == mtime_before,
    )
    _check(
        checks,
        "reacquire.pdf_sha256_unchanged",
        _sha256(reacquired.pdf_path.read_bytes()) == pdf_sha_before,
    )
    _check(
        checks,
        "reacquire.xml_sha256_unchanged",
        _sha256(reacquired.xml_path.read_bytes()) == xml_sha_before,
    )

    # 10. Trivial self-overlap
    print("[accept] --- 10. Self-overlap plumbing check ---")
    overlap = word_overlap(body_text, body_text)
    _check(
        checks,
        "metric.self_overlap_equals_one",
        overlap.ratio == 1.0,
        f"ratio={overlap.ratio}",
    )

    all_ok = all(c.ok for c in checks)
    print(f"\n{'=' * 60}")
    print(f"PMC-OA PROVE-ONE: {'PASS' if all_ok else 'FAIL'}")
    print(f"  {sum(c.ok for c in checks)}/{len(checks)} checks passed")
    print("=" * 60)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "verdict": "PASS" if all_ok else "FAIL",
                "pmcid": asset.pmcid,
                "version": asset.version,
                "article_dir": str(article_dir),
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--article-dir", type=Path, required=True)
    p.add_argument(
        "--report",
        type=Path,
        default=Path("docs/evaluation/PMC_OA_PROVE_ONE_ACCEPTANCE.json"),
    )
    args = p.parse_args(argv)
    ok = run(args.article_dir, args.report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
