# B1a-5b.2 Acquisition Completion Report

- **Emitted:** `2026-09-16T02:44:49+00:00`
- **Acquisition instrument commit:** `37c35d4d9bf1523b048dd3372c017d14b91eb12e` (merged in PR #183)
- **Selection manifest:** `docs/evaluation/DEV_PILOT_MANIFEST_V1.json`
- **Selection manifest SHA-256:** `6fc5d91da9cb896a49e396ea952b8b1aa417db422826bcd1512e9e58c69370b4`
- **Acquisition schema version:** `1`
- **Selection algorithm version:** `1`

## 20-document result table

| # | Corpus | Stratum | Canonical ID | Durable status | Execution disposition | Assets | Bytes | Receipt SHA-256 |
|---:|---|---|---|---|---|---:|---:|---|
| 1 | pmc_oa |  | `PMC3569185.1` | acquired | fresh | 3 | 351880 | `2d2ba2be4c2844e5...` |
| 2 | pmc_oa |  | `PMC4160324.1` | acquired | fresh | 3 | 2092459 | `6c358da5ed57f9ee...` |
| 3 | pmc_oa |  | `PMC12303665.1` | acquired | fresh | 3 | 743617 | `c4bb836e39569eeb...` |
| 4 | pmc_oa |  | `PMC10454006.1` | acquired | fresh | 3 | 1091890 | `9aba8c0a0ed05f7b...` |
| 5 | pmc_oa |  | `PMC7773825.1` | acquired | fresh | 3 | 5057102 | `12901018162b9957...` |
| 6 | pmc_oa |  | `PMC6839998.1` | acquired | fresh | 3 | 56596 | `064b93faff9a26bb...` |
| 7 | pmc_oa |  | `PMC5773191.1` | acquired | fresh | 3 | 1353366 | `54cc94090132ee71...` |
| 8 | pmc_oa |  | `PMC6374309.1` | acquired | fresh | 3 | 1199784 | `659faf68be6a4bb7...` |
| 9 | doclaynet |  | `3a504c7cb73621234b11...` | acquired | fresh | 3 | 574328 | `139d348d20e4ed7e...` |
| 10 | doclaynet |  | `601febfd3972ff8015d3...` | acquired | fresh | 3 | 232592 | `8ecdb6611fc0804b...` |
| 11 | doclaynet |  | `664f52bb603e7d3d399c...` | acquired | fresh | 3 | 568311 | `f56f84df0f2be77a...` |
| 12 | doclaynet |  | `8731e4b062bc18699744...` | acquired | fresh | 3 | 515930 | `ae048e02cf99051d...` |
| 13 | doclaynet |  | `a1652ae40baffccaf9a5...` | acquired | fresh | 3 | 402164 | `3afbf7f1762249ad...` |
| 14 | doclaynet |  | `7d2023bb74ead6e1e506...` | acquired | fresh | 3 | 394551 | `0caf99e65d8c2e90...` |
| 15 | federal_register | Rule | `2025-07879` | acquired | fresh | 3 | 251568 | `b341329b517788b1...` |
| 16 | federal_register | Rule | `2025-13505` | acquired | fresh | 3 | 219185 | `456d4f6c31914388...` |
| 17 | federal_register | Proposed Rule | `2025-11271` | acquired | fresh | 3 | 217287 | `8adb7c2c1431eb40...` |
| 18 | federal_register | Proposed Rule | `2025-16333` | acquired | fresh | 3 | 421373 | `8b97b651ab378d73...` |
| 19 | federal_register | Notice | `2025-19924` | acquired | fresh | 3 | 278270 | `c022c709fbe56b3b...` |
| 20 | federal_register | Notice | `2025-12869` | acquired | fresh | 3 | 232613 | `5909d3f7faa020bc...` |

## Asset counts and bytes by corpus

| Corpus | Docs | Assets | Bytes |
|---|---:|---:|---:|
| pmc_oa | 8 | 24 | 11946694 |
| doclaynet | 6 | 18 | 2687876 |
| federal_register | 6 | 18 | 1620296 |

## Federal Register XML availability (G2 adjudication support)

| Document | XML available |
|---|---|
| `2025-07879` | available |
| `2025-13505` | available |
| `2025-11271` | available |
| `2025-16333` | available |
| `2025-19924` | available |
| `2025-12869` | available |

## Fresh vs reused execution disposition

- fresh: **20/20**
- reused_verified: **0/20**

The durable on-disk status for every reused-verified receipt is `ACQUIRED` (verified by rehashing the receipt envelope and every asset from disk). "reused_verified" describes the *current run's* execution disposition, not what the receipt originally recorded.

## Terminal acceptance checklist

- [x] 20 durable ACQUIRED receipts written and re-verified from disk
- [x] PMC-OA 8/8 (metadata + PDF + JATS XML)
- [x] DocLayNet 6/6 (PNG + PDF + annotations) at pinned revision `0daf93102e2efce76c3e11a274a5e0d0969391d3`
- [x] Federal Register 6/6 (API metadata + authoritative PDF) with GovInfo package_id + granule_id verified; XML availability reported separately
- [x] 0 substitutions (no cutoff neighbor used, no CAL reach, no re-selection)
- [x] 0 identity mismatches
- [x] 0 integrity failures
- [x] 0 upstream-state changes
- [x] 0 unresolved acquisition failures
- [x] Every receipt binds to selection manifest SHA `6fc5d91da9cb896a49e396ea952b8b1aa417db422826bcd1512e9e58c69370b4`
- [x] Aggregate contains exactly the 20 selected canonical IDs
- [x] Post-run tests: ................................................................         [100%]
- [x] `docs/evaluation/DEV_PILOT_MANIFEST_V1.json` byte-identical to its pre-run bytes

## Statement of non-substitution

No canonical ID was substituted. The 20 acquired canonical IDs are exactly the 20 frozen in `DEV_PILOT_MANIFEST_V1.json` at SHA-256 `6fc5d91da9cb896a49e396ea952b8b1aa417db422826bcd1512e9e58c69370b4`. B1a-5a cutoff neighbors were not consulted. No fallback to another PMC version, another HF revision, or another Federal Register document occurred at any point.

## Statement of manifest invariance

The selection manifest `DEV_PILOT_MANIFEST_V1.json` was not written or modified during acquisition. Its SHA-256 recomputes to `6fc5d91da9cb896a49e396ea952b8b1aa417db422826bcd1512e9e58c69370b4` post-run, matching the pre-run attestation and the value recorded in `DEV_PILOT_FINALIZATION_PROVENANCE.md` (post-correction hash).

## Final recommendation

**B1a-5b.2 PASS.** All 20 selected documents have durable, byte-verifiable acquisition receipts. Raw payloads live under the gitignored `corpus/eval_v1/` tree; only provenance artifacts are being proposed for merge. B1a-6/B1a-7 remain out of scope for this checkpoint. Any subsequent step (parser runs, reviewer scaffolding) requires a separate authorization.
