# B1a-5b-V2 Acquisition Completion Report

- **Emitted:** `2026-09-16T04:30:36+00:00`
- **MANIFEST_V1 SHA-256:** `6fc5d91da9cb896a49e396ea952b8b1aa417db422826bcd1512e9e58c69370b4`
- **MANIFEST_V2 SHA-256:** `739fdbdbf68675a3a5fa1224e58c3c954d3e292837f81a358ecba6eeab43e127`
- **V1 receipts modified:** 0
- **payloads downloaded in V2:** 2 documents
- **payloads verified / reused:** 18 documents

## Provenance transition

|                     | V1 | V2 |
|---|---:|---:|
| PMC selected       | 8  | 8  |
| PMC retained       | —  | 6  |
| PMC removed        | —  | 2  |
| PMC new            | —  | 2  |
| DL retained        | —  | 6  |
| FR retained        | —  | 6  |

## PMC classification

- **Retained (6):** `PMC10454006.1`, `PMC12303665.1`, `PMC4160324.1`, `PMC5773191.1`, `PMC6374309.1`, `PMC7773825.1`
- **Removed by V2 (2):** `PMC3569185.1`, `PMC6839998.1`
- **New under V2 (2):** `PMC3544647.1`, `PMC8381808.1`

## 20-document result

| # | Corpus | Stratum | Canonical ID | Disposition | Receipt SHA-256 |
|---:|---|---|---|---|---|
| 1 | pmc_oa |  | `PMC4160324.1` | verified_payload_reuse | `4090a6a36791aadd...` |
| 2 | pmc_oa |  | `PMC12303665.1` | verified_payload_reuse | `e5b381e710fa62a1...` |
| 3 | pmc_oa |  | `PMC10454006.1` | verified_payload_reuse | `f55099834693dbb2...` |
| 4 | pmc_oa |  | `PMC7773825.1` | verified_payload_reuse | `2b36a3f7dba69442...` |
| 5 | pmc_oa |  | `PMC5773191.1` | verified_payload_reuse | `1674c1a0e96d6fdd...` |
| 6 | pmc_oa |  | `PMC6374309.1` | verified_payload_reuse | `795b89ae60aa7fd3...` |
| 7 | pmc_oa |  | `PMC8381808.1` | fresh_acquisition | `685d5c91607848a5...` |
| 8 | pmc_oa |  | `PMC3544647.1` | fresh_acquisition | `edc31febf6c29b69...` |
| 9 | doclaynet |  | `3a504c7cb73621234b11...` | verified_payload_reuse | `45f93f82dda30203...` |
| 10 | doclaynet |  | `601febfd3972ff8015d3...` | verified_payload_reuse | `83954344910a4927...` |
| 11 | doclaynet |  | `664f52bb603e7d3d399c...` | verified_payload_reuse | `36cb4f4436c352f7...` |
| 12 | doclaynet |  | `8731e4b062bc18699744...` | verified_payload_reuse | `c4fa506eaeaaab5e...` |
| 13 | doclaynet |  | `a1652ae40baffccaf9a5...` | verified_payload_reuse | `3460b7f013097c99...` |
| 14 | doclaynet |  | `7d2023bb74ead6e1e506...` | verified_payload_reuse | `66fe12abd2fa76a4...` |
| 15 | federal_register | Rule | `2025-07879` | verified_payload_reuse | `bcb41a9b3f530691...` |
| 16 | federal_register | Rule | `2025-13505` | verified_payload_reuse | `5cf13cdc70415a1b...` |
| 17 | federal_register | Proposed Rule | `2025-11271` | verified_payload_reuse | `bcf63faf37f461cc...` |
| 18 | federal_register | Proposed Rule | `2025-16333` | verified_payload_reuse | `e233fd10cfecaced...` |
| 19 | federal_register | Notice | `2025-19924` | verified_payload_reuse | `bc039362035c0f03...` |
| 20 | federal_register | Notice | `2025-12869` | verified_payload_reuse | `6f8cb6b44f883f32...` |

## Terminal acceptance checklist

- [x] 20 V2 receipts written (18 verified_payload_reuse, 2 fresh_acquisition)
- [x] Every V2 receipt binds to `MANIFEST_V2` SHA-256 above
- [x] Every retained payload re-hashed to match its V1 receipt
- [x] PMC retained: V2 oracle re-verify passed for all 6
- [x] PMC retained: acquired JATS SHA-256 == V2 inspection SHA-256
- [x] PMC new: fresh JATS SHA-256 == V2 inspection SHA-256
- [x] DocLayNet + Federal Register selections unchanged from V1
- [x] `MANIFEST_V1` byte-identical to pre-run bytes
- [x] `MANIFEST_V2` byte-identical to pre-run bytes
- [x] V1 acquisition evidence unchanged (20 V1 receipts, aggregate, completion report)
- [x] Post-run tests: .......................                                                  [100%]

## Statement of V1 preservation

Zero V1 receipts were modified. `DEV_PILOT_MANIFEST_V1.json`, its population artifacts, the V1 acquisition evidence chain, and the B1a-6 V1 failure evidence remain byte-identical to their pre-run state. The V2 acquisition chain is an independent set of receipts under `docs/evaluation/acquisition_v2/` and binds to `DEV_PILOT_MANIFEST_V2.json`.
