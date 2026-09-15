# PMC-OA AWS Cloud Reconnaissance (B1a-2)

**Status:** Read-only reconnaissance. No acquisition code has been written against these findings. Awaiting human approval of the acquisition contract before B1a-2 resumes.

**Date:** 2026-09-14
**Reason for reconnaissance:** The legacy PMC OA distribution channel — `oa_file_list.csv` at `/pub/pmc/` and the `oa.fcgi` per-article resolver — was removed during the week of 2026-08-24 per PMC's migration announcement. All PMC-OA data now flows through the AWS Open Data bucket `pmc-oa-opendata`.

---

## 1. Bucket schema

`s3://pmc-oa-opendata/` (anonymous HTTPS or unsigned S3; no AWS account required).

The bucket is **flat**, keyed by `<PMCID>.<version>/`. The 2020-era `oa_comm/` / `oa_noncomm/` / `oa_other/` prefix split is gone (matches PMC's stated new layout: license is determined from per-version metadata, not from a directory).

Per-article prefix `PMC<n>.<v>/` contains exactly these object shapes (verified on `PMC10000000.1`, `PMC10000002.1`, `PMC10000010.1`):

| Object | Content | Size range observed |
|---|---|---|
| `PMC<n>.<v>.json` | Per-version metadata (canonical source of truth) | ~700 B – 2 KB |
| `PMC<n>.<v>.pdf` | Article PDF | 800 KB – 5 MB |
| `PMC<n>.<v>.xml` | JATS XML full-text | 8 KB – 33 KB |
| `PMC<n>.<v>.txt` | PMC's pre-extracted plaintext (not authoritative for our oracle) | 4 KB – 25 KB |
| `<figid>.jpg` / `.png` etc. | Media / figure image assets | variable |

`.txt` is NLM's own extraction, not part of the JATS oracle. **We must continue to derive the textual oracle from the `.xml` (JATS)** — the `.txt` is convenient but out of scope for the `extraction_rules_version="1"` transformation the adapter already implements.

### PMCID + version representation in keys

- Prefix format: `<PMCID>.<version>/` where `<PMCID>` has the literal `PMC` prefix (e.g., `PMC10000000`) and `<version>` is an integer starting at `1`.
- Every object inside a `<PMCID>.<v>/` prefix is, by construction, part of the same article version. **The prefix boundary IS the version boundary.**
- PMCIDs with multiple versions appear as separate top-level prefixes (`PMC10000000.1/`, `PMC10000000.2/`, ...). None of the sampled PMCIDs in the observed range had a `.2` — but the shape is present in the schema.

---

## 2. Per-version metadata JSON schema

Every article has both an in-directory copy at `<PMCID>.<v>/<PMCID>.<v>.json` AND a flat-mirror copy at `metadata/<PMCID>.<v>.json`. They are byte-identical. The flat mirror enables enumeration without walking article directories.

Fields observed across all 8+ sampled records:

```
pmcid              str        e.g. "PMC10000000"  (no version suffix in the value)
version            int        e.g. 1
title              str
citation           str        e.g. "Chic Med Exam. 1867 Jan;8(1):54-64."
doi                str|null
pmid               int|null
license_code       str|null   observed values: "CC0", "CC BY", "CC BY-NC",
                              "CC BY-NC-ND", null; other CC variants likely
is_pmc_openaccess  bool
is_retracted       bool       ← retraction filter, no XML parsing needed
is_manuscript      bool       ← author-manuscript flag; native-published =  false
is_historical_ocr  bool       ← XML derived from OCR of scanned pages
                              (many early PMC-ID-range articles are 1867
                              journals; not native structured markup)
mid                str|null   author-manuscript ID
pdf_url            str        "s3://pmc-oa-opendata/<PMCID>.<v>/<PMCID>.<v>.pdf?md5=<hex>"
xml_url            str        "s3://.../<PMCID>.<v>.xml?md5=<hex>"
text_url           str        "s3://.../<PMCID>.<v>.txt?md5=<hex>"
media_urls         list[str]  each entry carries "?md5=<hex>"
```

Every asset URL embeds the file's MD5 as a query-string parameter. The S3 ETag for these objects **is the MD5** (single-part upload), so we can verify integrity either against the metadata's `?md5=` value or against the object's ETag — they agree.

---

## 3. Daily inventory reports

`inventory-reports/pmc-oa-opendata/metadata/YYYY-MM-DDT01-00Z/`

- **Retention observed:** 31 dated snapshots (2026-08-15 → 2026-09-14). Rolling ~30-day retention as PMC documented.
- **Manifest schema** (each dated folder contains a `manifest.json`):
  ```
  sourceBucket:        "pmc-oa-opendata"
  destinationBucket:   "arn:aws:s3:::pmc-oa-opendata"
  version:             "2016-11-30"  (S3 Inventory spec version, not corpus version)
  creationTimestamp:   unix-ms
  fileFormat:          "CSV"
  fileSchema:          "Bucket, Key, LastModifiedDate, ETag"
  files:               list[{key, size, MD5checksum}]  (gzipped CSV shards)
  ```
- **Data payload** (2026-09-14 snapshot): 5 gzipped CSV shards, ~250 MB compressed. Uncompressed rows list every object in the bucket (all article directories + `metadata/*.json` + `inventory-reports/*` themselves). One CSV shard per S3 partition; the manifest ties them together.
- There is **also** a Hive-partitioned mirror at `inventory-reports/pmc-oa-opendata/metadata/hive/` for Athena/Presto queries — not needed for our pipeline.

**Provenance implication:** the daily inventory is stable evidence of "the population as of this UTC date." Freezing an inventory snapshot alongside the pilot manifest gives us a reproducible population from which every deterministic selection can be replayed.

---

## 4. Exact-CC-BY filtering

Cleanly determinable from `metadata/<PMCID>.<v>.json` alone — no XML/PDF fetch required. The `license_code` field is a small controlled vocabulary; exact match `license_code == "CC BY"` is the same filter shape the previous plan used, and it can now be applied entirely against a tiny (~1 KB) per-article JSON.

Wide-range sampling (12 PMCID prefixes across the 5M–13M PMCID space):

| license_code | count in sample |
|---|---:|
| `"CC BY"` | 2 |
| `"CC BY-NC"` | 2 |
| `"CC BY-NC-ND"` | 2 |
| `"CC0"` | 1 |
| `null` / missing | 1 |
| (miss / 404 on non-existent PMCID prefix) | 4 |

Extrapolation to the full corpus is out of scope for reconnaissance, but the sample confirms:
- The value space is what PMC's docs describe.
- Exact `CC BY` articles exist in the population at non-trivial frequency.
- No article-content fetch is needed to make the license decision.

---

## 5. PDF ↔ XML same-version linkage

**Strong.** Both `pdf_url` and `xml_url` in `<PMCID>.<v>.json` reference S3 objects under the exact same `<PMCID>.<v>/` prefix. Since the prefix is the version boundary, same-version linkage is guaranteed by construction, not by heuristic. Additional layered evidence:

1. `metadata_json.pmcid` field must equal the PMCID part of the prefix (rejectable check).
2. `metadata_json.version` field must equal the version part of the prefix (rejectable check).
3. `pdf_url` and `xml_url` MD5 query-string values must match the actual ETag returned for those S3 objects (rejectable check).
4. JATS `<article-meta>/<article-id pub-id-type="pmc">` inside the XML must equal `pmcid` (already implemented by the offline `PmcOaV1Adapter`; not new).

This is materially stronger than the old `.tar.gz` model, where identity rested on co-membership in a single archive.

---

## 6. Stable provenance fields to record per selected article

Recommended manifest schema for the new PMC-OA acquisition (drop-in replacement for the current `manifest.json` fields — extension, not rewrite of the adapter contract):

```
schema_version:              "2"     ← bumped; new distribution mechanism
corpus:                      "pmc_oa"
pmcid:                       str
version:                     int
article_key_prefix:          str     e.g. "PMC10000000.1/"
acquired_utc:                ISO8601

distribution:
  source:                    "aws_open_data_pmc_oa"
  bucket:                    "pmc-oa-opendata"
  https_base_url:            "https://pmc-oa-opendata.s3.amazonaws.com/"
  inventory_snapshot_utc:    ISO8601 (from the inventory manifest we froze)
  inventory_manifest_sha256: hex

metadata_object:
  key:                       "PMC<n>.<v>/PMC<n>.<v>.json"
  sha256:                    hex   ← we compute after download; ETag is MD5
  etag:                      hex   ← S3-returned MD5 (single-part)
  size_bytes:                int

pdf:
  key:                       "PMC<n>.<v>/PMC<n>.<v>.pdf"
  size_bytes:                int
  sha256:                    hex   ← computed
  etag:                      hex   ← S3 MD5
  md5_from_metadata:         hex   ← from metadata_json.pdf_url ?md5=

xml:
  key:                       "PMC<n>.<v>/PMC<n>.<v>.xml"
  size_bytes:                int
  sha256:                    hex
  etag:                      hex
  md5_from_metadata:         hex
  pmcid_in_metadata:         str   ← JATS-internal PMCID for cross-check

license:
  license_code_from_metadata: str     e.g. "CC BY"
  license_type_from_xml:      str|null   (JATS article-meta/permissions)
  license_text_from_xml:      str|null

flags_from_metadata:
  is_pmc_openaccess:         bool
  is_retracted:              bool
  is_manuscript:             bool
  is_historical_ocr:         bool

citation:                    str    (from metadata JSON)
doi:                         str|null
pmid:                        int|null

selection:
  authorization:             "B1a-2"
  role:                      "corpus-adapter prove-one" | "B1 pilot"
  body_tokens:               int
  extraction_rules_version:  "1"
```

Any of these fields could be omitted only if measurably absent from the source; nothing is silently defaulted.

---

## 7. Smallest retrieval path (per prove-one article)

1. Download **one** inventory `manifest.json` (~1 KB) to freeze the population snapshot date.
2. Optionally download the inventory data shards (~250 MB gz). For **prove-one**, this step is not strictly required — we can walk `metadata/` alphabetically instead. For B1a-5's pilot selection it becomes the reproducibility anchor.
3. Walk `metadata/<PMCID>.<v>.json` in deterministic order (see §8), fetching one ~1 KB JSON per candidate.
4. First candidate that passes pre-filter (license + flags):
   - Fetch `<PMCID>.<v>/<PMCID>.<v>.xml` (10s of KB).
   - Run post-fetch structural check (article-type, sections, table-wrap, fig, body-token band).
   - If PASS: fetch `<PMCID>.<v>/<PMCID>.<v>.pdf` (~1 MB). Write `manifest.json`. DONE.
   - If FAIL: record + continue.

Total network cost for a successful prove-one: 1 inventory manifest + N × 1-KB JSON + 1 XML (~20 KB) + 1 PDF (~1 MB). Trivial.

---

## 8. Deterministic-selection pipeline (recommended)

Preserves the ordering primitive already used elsewhere in `eval_v1` (`SHA-256(pmcid) mod 100` per §8.2, and `SHA-256(pmcid)` as a total-order key for the prove-one selector).

Two viable enumeration sources, with different reproducibility guarantees:

**A. Inventory-anchored (recommended for B1a-5's 8-doc selection).**
- Freeze one inventory `manifest.json` at run time (record its SHA-256 + snapshot date).
- Download + hash all its data shards; verify each shard's MD5 matches the manifest.
- Extract all `Key` values matching `^PMC\d+\.\d+/PMC\d+\.\d+\.json$` — this is the full per-version metadata population, one row per article version.
- Sort eligible PMCID.v strings by `SHA-256("PMCID.v")` ascending.
- Walk the ordered list, fetch each metadata JSON, apply filters, apply post-fetch checks.

**B. Metadata-mirror walk (fine for prove-one).**
- Skip the inventory download entirely.
- Paginate `metadata/` prefix. Sort observed keys by SHA-256 order in memory.
- Same filtering + post-fetch pipeline.
- Reproducibility caveat: the "population" is defined by whatever `metadata/` contains at the moment of the run rather than a frozen daily snapshot. For prove-one that's acceptable; for the 8-doc pilot, prefer A.

**Pre-filters (metadata-only, no XML/PDF fetch):**
- `license_code == "CC BY"` (exact — locked)
- `is_retracted == false`
- `is_manuscript == false` (author-manuscript preprints have different structural provenance)
- `is_historical_ocr == false` (JATS derived from OCR of scanned pages is not native structured markup and would undermine "textual G1 oracle" semantics)
- `is_pmc_openaccess == true` (redundant with license but explicit)

The `is_historical_ocr` filter is **new** — the field did not exist in the legacy pipeline. Its presence exposes that a nontrivial fraction of PMC-OA content (visible in low PMCID ranges: the sampled `PMC10000000.1` is a *Chicago Medical Examiner* article from **1867**) is OCR'd scans, not native XML. Including such articles would silently degrade textual-G1 semantics. Recommend making this filter **required** for B1a-2 and B1a-5.

**Post-fetch checks (unchanged from the earlier plan, and already implementable against `_transform_jats`):**
- Package integrity: metadata `pmcid` == prefix PMCID; metadata `version` == prefix version; PDF MD5 == metadata's `?md5=`; XML MD5 == metadata's `?md5=`.
- JATS `article-type == "research-article"`.
- Not retraction / correction (both metadata flag AND JATS subject sweep, cheap belt-and-braces).
- `<abstract>` present; ≥ 2 `<sec>`; ≥ 1 `<table-wrap>`; ≥ 1 `<fig>`.
- Canonical body tokens in `[2_000, 15_000]` (engineering eligibility bound, not a population percentile).

---

## 9. Anonymous access — confirmed

All reconnaissance queries above were plain HTTPS `GET` / `LIST` against `https://pmc-oa-opendata.s3.amazonaws.com/` with no `boto3` and no AWS credentials. Sufficient for the pipeline. No dependency additions required.

---

## 10. Adapter-side impact

`benchmarks/eval_v1/adapters/pmc_oa_v1.py` is **unaffected**. It consumes `PmcOaAsset` + per-article `manifest.json` and does not know or care where those came from. The offline adapter contract, all 7 passing unit tests, and the `extraction_rules_version="1"` JATS transformation all stand.

Following your `PmcOaAsset` guidance from earlier, the asset shape may usefully evolve to:

```python
@dataclass(frozen=True)
class PmcOaAsset:
    pmcid: str
    version: int
    pdf_path: Path
    xml_path: Path
    metadata_json_path: Path   # ← NEW; the per-version JSON is authoritative
    manifest_path: Path
```

`metadata_json_path` gives the adapter cheap access to the canonical per-version fields (license, flags, S3 URLs, MD5s) without re-parsing JATS for them. Optional — proposed for the acquisition-rewrite PR, not this recon.

---

## 11. Recommendation

- **Distribution channel:** `pmc-oa-opendata` S3 bucket via anonymous HTTPS. No API keys, no `boto3`, no dependency changes.
- **Provenance anchor:** daily inventory `manifest.json` (~1 KB) frozen at pipeline run time. Data-shard hashes verified. Snapshot date + SHA-256 recorded in every per-article manifest we write.
- **Identity model:** per-version prefix `<PMCID>.<v>/`, cross-verified via metadata JSON `pmcid` / `version`, MD5-query-string vs S3 ETag, and (unchanged) JATS internal PMCID.
- **Enumeration:** inventory-anchored for B1a-5; metadata-mirror walk fine for the immediate prove-one.
- **License filter:** unchanged (`license_code == "CC BY"`).
- **New required filters:** `is_retracted == false`, `is_manuscript == false`, `is_historical_ocr == false`.
- **Adapter contract:** unchanged; `PmcOaAsset` gains an optional `metadata_json_path` field.
- **Prove-one contamination ledger:** the failed 2026-09-14 selection attempt did **not** cache or inspect any article; no ledger entries needed.

## 12. What has NOT been touched

- No new acquisition code has been written against these findings.
- No commits made. The existing `benchmarks/eval_v1/acquisition/pmc_oa.py` and `pmc_oa_select.py` (targeting the dead endpoints) remain uncommitted on branch `b1a/pmc-oa-adapter` and will be replaced wholesale once the acquisition contract is approved.
- No changes to `PROTOCOL_V1.md`.
- No memory updates.

**STOP for human review.**
