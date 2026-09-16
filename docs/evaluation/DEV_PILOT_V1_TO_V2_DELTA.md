# B1 Pilot Selection V1 → V2 Delta

The V2 PMC re-selection recomputed the PMC-OA component under the textual-oracle eligibility predicate (version `2`, min body tokens `500`). DocLayNet and Federal Register selections were preserved byte-for-byte.

## Summary counts

- V1 PMC selected: 8
- V2 PMC selected: 8
- Retained: 6
- Removed by V2 eligibility: 2
- Newly selected under V2: 2

## Retained (V1 selected AND V2 selected)

| Canonical ID |
|---|
| `PMC10454006.1` |
| `PMC12303665.1` |
| `PMC4160324.1` |
| `PMC5773191.1` |
| `PMC6374309.1` |
| `PMC7773825.1` |

## Removed by V2 eligibility (V1 selected, V2 rejected)

| Canonical ID | V2 reason |
|---|---|
| `PMC3569185.1` | `canonical_body_tokens_lt_500:265` |
| `PMC6839998.1` | `jats_body_absent` |

## Newly selected under V2 (V2 selected, not in V1 selected)

| Canonical ID |
|---|
| `PMC3544647.1` *(was V1 cutoff neighbor)* |
| `PMC8381808.1` *(was V1 cutoff neighbor)* |

## All V1-inspected PMC canonical IDs — V2 decisions

| V1 canonical ID | V1 status | V2 decision |
|---|---|---|
| `PMC3569185.1` | selected | V2 reject: `canonical_body_tokens_lt_500:265` |
| `PMC4160324.1` | selected | V2 eligible |
| `PMC12303665.1` | selected | V2 eligible |
| `PMC10454006.1` | selected | V2 eligible |
| `PMC7773825.1` | selected | V2 eligible |
| `PMC6839998.1` | selected | V2 reject: `jats_body_absent` |
| `PMC5773191.1` | selected | V2 eligible |
| `PMC6374309.1` | selected | V2 eligible |
| `PMC8381808.1` | neighbor | V2 eligible |
| `PMC3544647.1` | neighbor | V2 eligible |
| `PMC3328247.1` | neighbor | V2 eligible |
| `PMC7256328.1` | neighbor | V2 eligible |
