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
| Unique source documents in selection | 53 (of 280 selected pages; multiple pages per document are included) |
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

**Unit of selection: pages, not source documents.** The selection operates at the
page_hash level — the canonical DocLayNet unit.  The 280 selected pages come from
53 unique source documents (original_filename); multiple pages from the same document
are included (e.g., 41 pages from `perl-all-en-5.8.5.pdf`).  The §6.2 cluster-correction
reasoning (DE = 2.2, n_raw = 1,320 pages from 264 documents) was the original design
intent for document-level cluster sampling.  The implementation selects at the page level
because the eligibility filter and adapter are page-level.  The eligible pool of 821 pages
from 53 source documents is the effective universe; 280 pages were selected from it.

**Relationship to §6.2 clustering analysis:** The freeze manifest reasoned about
"280 documents" with mean cluster size m̄ = 5.  The page-level implementation
is a simplification: it selects 280 pages (not 280 documents × their full page sets).
This is recorded here as the pre-execution scope decision; any follow-on study that
requires document-level cluster sampling should re-implement accordingly.

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
| `FinTabNet.c-PDF_Annotations.tar.gz` | 255,009,483 bytes (243 MiB) | `bc32348eb1e73a6a8207f41267eb1b5a81e6ef5b0ff79d656f53d5f2e36ce60f` | Downloaded and byte-verified (2026-09-16) |
| `FinTabNet.c-Structure.tar.gz` | 3,173,052,423 bytes (3,026 MiB) | `bde6a9443e08f6c94f8cf65fcd439d516131ede49c59ed2279959a8be3d0fbf6` | Downloaded and byte-verified (2026-09-16) |

**LFS OID provenance note:** Git LFS stores each file's SHA-256 as the `lfs.oid` in the
repository tree.  This OID is identical to the output of `sha256sum` on the downloaded
archive and is resolved from the pinned revision `e5673a90`.  Both archives were
downloaded and byte-verified against the LFS OIDs on 2026-09-16 before selection was run.

**Coverage per manifest §6.3:** 500 tables, two-tier stratified sample (SIMPLE / COMPOUND).
Multi-page spanning removed as a pre-execution corpus-capability correction (B1a-8b): the
FinTabNet.c V1 ground truth contains no field identifying cross-page cell continuations;
the tier cannot be determined mechanically.  See §6.3 of STUDY_FREEZE_MANIFEST_V1.md for
full correction record and frozen tier definitions.

**Eligible pool (val split, post-exclusion):** 3,714 SIMPLE + 5,936 COMPOUND = 9,650 tables
(0 excluded by `exclude_for_structure`).  Largest-remainder allocation: SIMPLE=192, COMPOUND=308.

**Selection provenance:**

| File | Path | SHA-256 |
|---|---|---|
| Selection manifest | `docs/evaluation/STAGE1_FINTABNET_C_SELECTION.json` | `f8ec903aecdae4a0c49484d42539b220d941d19ce79e5489f62796c02062427e` |

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
| FinTabNet.c | `e5673a9` | LFS OID (= archive SHA-256) + selection manifest SHA-256 | COMPLETE |
| OmniDocBench | `91fe284` | N/A | EXCLUDED |

**All corpus pins were resolved and verified before any parser execution.** Both FinTabNet.c
archives were downloaded and byte-verified against their LFS OIDs on 2026-09-16.
500-table selection is complete; selection manifest SHA-256 recorded in §3 above.

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

3. **FinTabNet.c two-tier stratification** (B1a-8b, pre-execution corpus-capability correction):
   The original three-tier design (simple / compound / multi-page spanning) was reduced to
   two tiers.  Inspection of the FinTabNet.c V1 ground truth confirmed that cross-page
   spanning cannot be determined mechanically from the annotations.  Frozen definitions:
   SIMPLE = every cell `len(row_nums)==1` and `len(column_nums)==1`; COMPOUND = any cell
   `len(row_nums)>1` or `len(column_nums)>1`.  Allocation: SIMPLE=192, COMPOUND=308 (of 500
   total), deterministic largest-remainder.  Full correction record in §6.3 of
   STUDY_FREEZE_MANIFEST_V1.md.  Selection complete; 500 tables frozen.

All decisions were made and recorded before any parser execution began.
