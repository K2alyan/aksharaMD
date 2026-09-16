# B1a-6-V2 Ingestion Validation Completion Report

- **Emitted:** `2026-09-16T05:08:20+00:00`
- **Instrument commit:** `0d627a9bf1f97380580da129cafbfda73843fc5e`
- **MANIFEST_V2 SHA-256:** `739fdbdbf68675a3a5fa1224e58c3c954d3e292837f81a358ecba6eeab43e127`
- **Ingestion schema version:** `2`

## Per-corpus matrix (V2)

| Corpus | Expected | Source valid | GT/support valid | Provenance valid | V2 invariant | Result |
|---|---:|---:|---:|---:|---:|---|
| pmc_oa | 8 | 8/8 | 8/8 | 8/8 | 8/8 | PASS |
| doclaynet | 6 | 6/6 | 6/6 | 6/6 | N/A | PASS |
| federal_register | 6 | 6/6 | 6/6 | 6/6 | N/A | PASS |

## Per-document result

| # | Corpus | Canonical ID | Adapter | GT kind | Receipt SHA-256 | Result |
|---:|---|---|---|---|---|---|
| 1 | pmc_oa | `PMC4160324.1` | PmcOaV1Adapter | `xml_full_text` | `8b20e9f444b75932...` | PASS |
| 2 | pmc_oa | `PMC12303665.1` | PmcOaV1Adapter | `xml_full_text` | `b6ec87a3451dfa55...` | PASS |
| 3 | pmc_oa | `PMC10454006.1` | PmcOaV1Adapter | `xml_full_text` | `e669a066047d68d3...` | PASS |
| 4 | pmc_oa | `PMC7773825.1` | PmcOaV1Adapter | `xml_full_text` | `5ebef8a745ff73f3...` | PASS |
| 5 | pmc_oa | `PMC5773191.1` | PmcOaV1Adapter | `xml_full_text` | `9dc4a76525d23a59...` | PASS |
| 6 | pmc_oa | `PMC6374309.1` | PmcOaV1Adapter | `xml_full_text` | `f6815f1cf62aadcb...` | PASS |
| 7 | pmc_oa | `PMC8381808.1` | PmcOaV1Adapter | `xml_full_text` | `95e4cde361aa0f60...` | PASS |
| 8 | pmc_oa | `PMC3544647.1` | PmcOaV1Adapter | `xml_full_text` | `32755f00e7ec300e...` | PASS |
| 9 | doclaynet | `3a504c7cb73621234b11...` | DocLayNetV1Adapter | `bbox_layout` | `a0363460cbd8ff10...` | PASS |
| 10 | doclaynet | `601febfd3972ff8015d3...` | DocLayNetV1Adapter | `bbox_layout` | `949fbbdef3b1329a...` | PASS |
| 11 | doclaynet | `664f52bb603e7d3d399c...` | DocLayNetV1Adapter | `bbox_layout` | `4ca96520967d1304...` | PASS |
| 12 | doclaynet | `8731e4b062bc18699744...` | DocLayNetV1Adapter | `bbox_layout` | `11d2812a7b9b8b11...` | PASS |
| 13 | doclaynet | `a1652ae40baffccaf9a5...` | DocLayNetV1Adapter | `bbox_layout` | `6982f5e167688117...` | PASS |
| 14 | doclaynet | `7d2023bb74ead6e1e506...` | DocLayNetV1Adapter | `bbox_layout` | `13100c754ee90cfb...` | PASS |
| 15 | federal_register | `2025-07879` | FederalRegisterV1Adapter | `fpr_baseline` | `27724cd2ad7147a8...` | PASS |
| 16 | federal_register | `2025-13505` | FederalRegisterV1Adapter | `fpr_baseline` | `e5078672e0c8ed41...` | PASS |
| 17 | federal_register | `2025-11271` | FederalRegisterV1Adapter | `fpr_baseline` | `fb54aedcd1fcc26f...` | PASS |
| 18 | federal_register | `2025-16333` | FederalRegisterV1Adapter | `fpr_baseline` | `2c0ebeda67199221...` | PASS |
| 19 | federal_register | `2025-19924` | FederalRegisterV1Adapter | `fpr_baseline` | `5b3974546b7c834d...` | PASS |
| 20 | federal_register | `2025-12869` | FederalRegisterV1Adapter | `fpr_baseline` | `9ad80deec618a772...` | PASS |

## Final result

**B1a-6-V2: PASS**

All 20 acquired V2 documents ingest cleanly through their V1 corpus adapters and satisfy every source, ground-truth/support, and provenance contract locked in PROTOCOL_V1 plus the V2 PMC textual-oracle invariant. No parser was run, no AksharaMD score was produced, and no adapter, receipt, or selection artifact was modified during this validation.

## Comparison with B1a-6 V1

> V1 stopped at 6/20 because PMC metadata eligibility admitted documents that could not supply the intended full-text G1 oracle (specifically PMC6839998.1, an abstract-only record with no `<body>`). V2 introduced a predeclared body-based oracle eligibility rule before re-selection (PR #186 and merged as `b1c8763`), then re-selected PMC (PR #187 merged as `875bfd2`), acquired the 20-document V2 pilot (PR #188 merged as `fe7dc38`), and B1a-6-V2 then validated whether the repair holds across the complete re-selected pilot.

This report claims the repair holds **for this pilot's re-selected 20**. It does not claim the V2 predicate is generally validated beyond this pilot.

- Post-run tests: 95 passed in 0.87s
