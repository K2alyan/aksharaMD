# Stage 1 Corpus Snapshot Manifest V1
## AksharaMD Validation Study — B1a-8

**Produced:** 2026-09-16  
**Study freeze:** STUDY_FREEZE_MANIFEST_V1.md (AUTHORIZED 2026-09-16)  
**Branch:** b1a-8/corpus-snapshot-v1  
**Status:** PRE-EXECUTION — corpus provenance locked before any parser runs

This document records the byte-level provenance chain for all Track A and Track C
corpora.  It is produced before any parser execution and constitutes the corpus
snapshot entry required by §8 of STUDY_FREEZE_MANIFEST_V1.

---

## 1. olmOCR-Bench (Track A — primary assertion corpus)

| Field | Value |
|---|---|
| Corpus | olmOCR-Bench |
| Source | Hugging Face: `allenai/olmOCR-bench` |
| HF dataset revision | `54a96a6fb6a2bd3b297e59869491db4d3625b711` |
| License | ODC-BY |
| Files verified | 1,412 / 1,412 |
| Local path | `tmp/olmocr-full-data/` |
| Acquisition receipt | `tmp/olmocr-full-data/acquisition.json` |
| Acquisition receipt SHA-256 | `ca9aa04e6b91ad00a2cf9c0e49e0512de7adb5e2c3b45a0bbd1adf2d629cfe58` |

**Verification method:** SHA-256 of each file computed against on-disk bytes and matched to the
inventory extracted from the HF dataset at revision `54a96a6`.  All 1,412 files verified.

**Coverage per manifest §6.1:** Full corpus.  1,403 PDFs, 7,010 unit-test assertions.

---

## 2. DocLayNet (Track A — table detection and structural-association GT)

| Field | Value |
|---|---|
| Corpus | DocLayNet v1.2 |
| Source | Hugging Face: `docling-project/DocLayNet-v1.2` |
| HF dataset revision | `0daf93102e2efce76c3e11a274a5e0d0969391d3` |
| Split | `validation` |
| Shards scanned | 7 / 7 |
| Total rows in val split | 6,489 |
| Eligible pages (post-filter) | 821 |
| Exclusion ledger entries removed | 0 (222 ledger entries; all are train-split IDs) |
| Selected pages | 280 |
| Selection algorithm | `stratified_by_doc_category_sha256_rank` |
| Freeze seed used | `6c270ac293b348ca27279bdd012375aa6c707be494085e70781637a99a6322fa` |
| License | CDLA-Permissive |

**Eligibility filter (locked B1a-3):** `MIN_ANNOTATIONS = 10`, `MAX_ANNOTATIONS = 200`,
≥1 Table region, ≥1 additional non-Text structural region.

**Stratification by doc_category (proportional to pool prevalence):**

| doc_category | Pool eligible | Selected | Pool % |
|---|---|---|---|
| financial_reports | 473 | 161 | 57.6% |
| manuals | 140 | 48 | 17.1% |
| patents | 78 | 27 | 9.5% |
| government_tenders | 58 | 20 | 7.1% |
| laws_and_regulations | 51 | 17 | 6.2% |
| scientific_articles | 21 | 7 | 2.6% |
| **Total** | **821** | **280** | |

**Provenance files:**

| File | Path | SHA-256 |
|---|---|---|
| Population checkpoint | `docs/evaluation/STAGE1_DOCLAYNET_VAL_POPULATION.json` | `66abd1655f3e2585cdeb38e2fb78c971fad4721e90a563c65af1d2ac551f19ae` |
| Selection manifest | `docs/evaluation/STAGE1_DOCLAYNET_VAL_SELECTION.json` | `efbe3729121117ad5d03e715605bc84eef295ca701d5ff238b33a175981b2df2` |

**Coverage per manifest §6.2:** Stratified sample of 280 documents from the val split.
Note: the eligible pool (821 pages) is below the theoretical n_raw = 1,320 from the
sampling-math section.  This is expected: the eligibility filter (must have ≥1 Table region
plus additional structural regions) selects a restricted subpopulation of pages.  All 821
eligible pages are drawn from a pool that uniformly satisfies the filter; the 280 selected
are the top-ranked per category under the freeze-seed sort.

---

## 3. FinTabNet.c (Track A — table cell fidelity GT, TEDS metric)

| Field | Value |
|---|---|
| Corpus | FinTabNet.c |
| Source | Hugging Face: `bsmock/FinTabNet.c` |
| HF dataset revision | `e5673a90b98d02c4832f9e836d72762f0e8933a0` |
| License | CDLA-Permissive-2.0 |
| Local path | `corpus/eval_v1/fintabnet_c/` |
| Acquisition receipt | `corpus/eval_v1/fintabnet_c/acquisition.json` |
| Acquisition receipt SHA-256 | `7083670bc10e13ac158d8ce21a5df0b251f29b1bd2e199d504cc75780d2c0a07` |

**Archive provenance (Git LFS OIDs = SHA-256 of archive bytes):**

| Archive | Size | LFS SHA-256 (= content SHA-256) | Download status |
|---|---|---|---|
| `FinTabNet.c-PDF_Annotations.tar.gz` | 255,009,483 bytes (243 MiB) | `bc32348eb1e73a6a8207f41267eb1b5a81e6ef5b0ff79d656f53d5f2e36ce60f` | OID resolved; not yet downloaded |
| `FinTabNet.c-Structure.tar.gz` | 3,173,052,423 bytes (3,026 MiB) | `bde6a9443e08f6c94f8cf65fcd439d516131ede49c59ed2279959a8be3d0fbf6` | OID resolved; not yet downloaded |

**LFS OID provenance note:** Git LFS stores each file's SHA-256 as the `lfs.oid` in the
repository tree.  This OID is identical to the output of `sha256sum` on the downloaded
archive and is resolved from the pinned revision `e5673a90` without downloading the
archives.  Download and byte-level verification must occur before any parser execution;
run `python -m benchmarks.eval_v1.acquisition.fintabnet_c_hf --download` to fetch and
verify both archives.

**Coverage per manifest §6.3:** 500 tables, stratified sample by table complexity tier
(simple / compound / multi-page spanning).  Table selection is the next acquisition step
after archive download; selection script:
`benchmarks/eval_v1/selection/stage1_select_fintabnet_c.py` (to be written at execution
start).

---

## 4. OmniDocBench — excluded from Track A

| Field | Value |
|---|---|
| Corpus | OmniDocBench v1.5 |
| Source | Hugging Face: `lllcho/OmniDocBench` |
| HF dataset revision | `91fe284bbfacfa687959ae3eb00846ca852aa907` |
| Evaluator code commit | `193627ae` (historical reference only) |
| Exclusion reason | Corpus-format incompatibility: v1.5 consists of 981 JPEGs + 377 PNGs; no PDFs; all four frozen parsers require PDF input |
| Track A execution | EXCLUDED |
| V1 results | None (no parser execution on this corpus) |

See §6.4 of STUDY_FREEZE_MANIFEST_V1.md for full rationale.

---

## 5. Track C corpora (QASPER, MMLongBench-Doc, TAT-DQA)

Track C corpora are standard benchmark datasets accessed at execution time.  They do not
require pre-execution download or provenance pinning in this manifest because:
- QASPER and TAT-DQA use publicly archived splits with immutable identifiers in the
  benchmark literature.
- MMLongBench-Doc is accessed via the frozen research distribution.

Track C corpus provenance is recorded in the Track C execution manifest at execution start.

---

## 6. Snapshot integrity summary

| Corpus | HF Revision | Provenance type | Status |
|---|---|---|---|
| olmOCR-Bench | `54a96a6` | File-level SHA-256 (1,412 verified) | COMPLETE |
| DocLayNet val | `0daf931` | Selection manifest SHA-256 | COMPLETE |
| FinTabNet.c | `e5673a9` | LFS OID (= archive SHA-256) | OID RESOLVED; download pending |
| OmniDocBench | `91fe284` | N/A | EXCLUDED |

**All corpus pins were resolved before any parser execution.** The FinTabNet.c archive
download must be verified before any FinTabNet.c parser run begins.

---

## 7. Amendment record

This document records two pre-execution corpus decisions that refine the Study Freeze
Manifest V1 without altering the methodological design:

1. **OmniDocBench excluded from Track A** (incompatibility discovered pre-execution):
   The v1.5 corpus is image-only; parsers require PDF input.  Documented in §6.4 of
   STUDY_FREEZE_MANIFEST_V1.md.  This removes OmniDocBench from the executable Track A
   run; it does not change any claim, metric, or analysis plan.

2. **FinTabNet.c confirmed** (pre-execution corpus selection):
   The "PubTabNet or FinTabNet" TBD in the original freeze is resolved:
   `bsmock/FinTabNet.c` at `e5673a90`, CDLA-Permissive-2.0, PDFs included.
   PubTabNet excluded (image-only; no PDFs).  Version pinned in §8 of
   STUDY_FREEZE_MANIFEST_V1.md.

Both decisions were made and recorded before any parser execution began.
