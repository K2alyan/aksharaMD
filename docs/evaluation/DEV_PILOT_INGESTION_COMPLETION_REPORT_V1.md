# B1a-6 Ingestion Validation Completion Report

- **Emitted:** `2026-09-16T03:11:38+00:00`
- **Instrument commit:** `42a6ba29d29d6939278e61270b7d111a7b61266d`
- **Selection manifest SHA-256:** `6fc5d91da9cb896a49e396ea952b8b1aa417db422826bcd1512e9e58c69370b4`
- **Ingestion schema version:** `1`

## Scope statement

**B1a-6 stopped at document 6/20. Fourteen documents were not evaluated
and therefore no conclusion about their ingestion validity is made.**

This report records exactly what was observed for the six documents
that were validated (5 PASS, 1 INSTRUMENT_CHANGE_REQUIRED), the
specific check that failed, and the classification of the finding.
It does not attempt to characterize the remaining 14.

## Per-corpus matrix

| Corpus | Expected | Validated | Source valid | GT/support valid | Provenance valid | Result |
|---|---:|---:|---:|---:|---:|---|
| pmc_oa | 8 | 6 (5 PASS + 1 FAIL) | 6/6 | 5/6 | 6/6 | FAIL |
| doclaynet | 6 | 0 | — | — | — | NOT EVALUATED |
| federal_register | 6 | 0 | — | — | — | NOT EVALUATED |

"NOT EVALUATED" means the run stopped before those corpora were
reached; nothing was checked, nothing failed. This is not a corpus
outcome.

## Per-document result

| # | Corpus | Canonical ID | Adapter | GT kind | Receipt SHA-256 | Result |
|---:|---|---|---|---|---|---|
| 1 | pmc_oa | `PMC3569185.1` | PmcOaV1Adapter | `xml_full_text` | `6604092fb0640c08…` | PASS |
| 2 | pmc_oa | `PMC4160324.1` | PmcOaV1Adapter | `xml_full_text` | `05f66bbcd5c46414…` | PASS |
| 3 | pmc_oa | `PMC12303665.1` | PmcOaV1Adapter | `xml_full_text` | `67709adca7d0801f…` | PASS |
| 4 | pmc_oa | `PMC10454006.1` | PmcOaV1Adapter | `xml_full_text` | `6d591644a0ac1404…` | PASS |
| 5 | pmc_oa | `PMC7773825.1` | PmcOaV1Adapter | `xml_full_text` | `82c03f9f14c7cb5f…` | PASS |
| 6 | pmc_oa | `PMC6839998.1` | PmcOaV1Adapter (partial) | `xml_full_text` (returned) | `3c1f082e12c11a14…` | INSTRUMENT_CHANGE_REQUIRED |
| 7…20 | — | 14 documents | — | — | — | NOT EVALUATED |

## Failure classification

**PMC-OA population eligibility did not guarantee availability of the
textual G1 oracle required by the V1 corpus role.**

This is a **selection-instrument defect** — the B1a-5a eligibility
predicate for PMC-OA established licensing/distribution properties
(CC BY, open access, not retracted, not manuscript, not historical
OCR, PDF present, XML present) but did **not** establish that the
acquired JATS contained the textual-oracle content that
`PmcOaV1Adapter.ingest_ground_truth` is contractually required to
produce.

It is **not** an adapter defect. `PmcOaV1Adapter` behaved correctly:
it returned `kind = "xml_full_text"`, `data["body_text"] = ""`, and
declared its extraction stats truthfully. The empty body_text is a
faithful reflection of the underlying JATS, which has
`article-type = "abstract"` and no `<body>` element (0 sections,
0 table-wraps, 0 figures).

## Failure record

- **Stopped at:** `PMC6839998.1` (pmc_oa)
- **Failed check:** `gt.body_text_nonempty` — the extracted `body_text`
  was empty; every preceding check (adapter instantiation, source
  identity, source SHA matching the acquisition receipt, GT kind =
  `xml_full_text`, `gt.returned`, `gt.doc_id_matches`) passed.
- **Failure receipt:** `docs/evaluation/ingestion_v1/pmc_oa/PMC6839998.1.json`
- **Failure receipt SHA-256:** `3c1f082e12c11a144a73c0e911cebed893a7ab36dd0bf084943d507ddc105836`
- **Not run (remaining after stop):** 14 documents (2 remaining
  PMC-OA, 6 DocLayNet, 6 Federal Register). Per contract, no
  substitution, no cutoff neighbor, no adapter change during the run.

## What was NOT done

- No modification to any V1 corpus adapter
  (`benchmarks/eval_v1/adapters/*.py`).
- No modification to any B1a-5a artifact
  (`DEV_PILOT_MANIFEST_V1.json`, checkpoints, snapshots, exclusion
  union, provenance record).
- No modification to any B1a-5b.2 acquisition receipt.
- No substitution of a B1a-5a cutoff neighbor.
- No re-run of B1a-5a selection.
- No bump of `selection_algorithm_version` in this PR.
- No repair of the JATS content.

## Final result

**B1a-6: FAIL — INSTRUMENT CHANGE REQUIRED**

The reviewer must decide the methodological response before B1a-6 is
re-run. This PR ships only the failure evidence.
