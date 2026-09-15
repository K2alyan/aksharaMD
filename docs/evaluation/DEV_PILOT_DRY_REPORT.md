# B1 Pilot Dry-Selection Report (B1a-5a)

**Emitted:** 2026-09-15T20:24:55+00:00
**Selection algorithm version:** `1`
**Manifest:** `docs\evaluation\DEV_PILOT_MANIFEST_V1.json`

## Population snapshots (frozen inputs, written before selection)

```json
{
  "checkpoint_schema_version": "1",
  "doclaynet": {
    "dataset_id": "docling-project/DocLayNet-v1.2",
    "eligibility_evaluation": "eager_full_materialization",
    "ineligible_head": [
      {
        "canonical_id": "f446422ed85e300319d3aff929762b8527cbc9ec26f55703b36df3e7447cc2d5",
        "reason": "n_annotations_lt_10:9"
      },
      {
        "canonical_id": "f77ae4702c4b8635b13ce0eed4f0f69ef91b28af4172c361b66772f67111b40a",
        "reason": "n_annotations_lt_10:9"
      },
      {
        "canonical_id": "29c328237fb62b57ec24466e93c37006b5cc2b917e8ad1b2cfaf0a45aeeed4d5",
        "reason": "n_annotations_lt_10:4"
      },
      {
        "canonical_id": "82b2f531d84fd47710d978cc3e6e51b3e5bc248a907bbadce1df72fdbd3a4106",
        "reason": "n_annotations_lt_10:8"
      },
      {
        "canonical_id": "de9ce284f3e8ab346b2d714767ef395b1c0c0d265607f003b8b822c8a7339110",
        "reason": "no_table_region"
      },
      {
        "canonical_id": "bed84c3a3d2299c4b8f4b34b662c7d148dd52c56b7e010adc39f4ab6a1d701b2",
        "reason": "no_table_region"
      },
      {
        "canonical_id": "60df1426f735eb4196c830b8a327702efe502bc804615d8231d84e17c1210554",
        "reason": "no_table_region"
      },
      {
        "canonical_id": "ed264294ee65eaa5ac2bed45d4ebdc85d71fc990e89a20ae8c33d150ef95a553",
        "reason": "no_table_region"
      },
      {
        "canonical_id": "0f2609ec06b30718b1f117f3c045c9e7032db398bd2a625b7683168602769596",
        "reason": "no_table_region"
      },
      {
        "canonical_id": "5630a8127730b7faa4d905fb8eb5057461d51bc48f767da4dc58dc977b9bab3b",
        "reason": "n_annotations_lt_10:5"
      },
      {
        "canonical_id": "d1b79bd332e42e0a1e633aee4848a15a6b155877fe30da7a471b4130d6f04ee5",
        "reason": "n_annotations_lt_10:5"
      },
      {
        "canonical_id": "92e0883adc992df38e979f59f78666cb9cba8620589e3acc7760268674d9dcc1",
        "reason": "n_annotations_lt_10:7"
      },
      {
        "canonical_id": "b71e87ee35477ce6a355d46a616ccf7dacb780d43ca9079225d4a52d4e8072ff",
        "reason": "n_annotations_lt_10:8"
      },
      {
        "canonical_id": "9fbb1daac28ac33ad46069d10c1c6199866a9556f0575cfa0d8aae1b30014ac4",
        "reason": "no_table_region"
      },
      {
        "canonical_id": "5f60e9b25addf103128346c10cf36e2dd75a6c55e32b7e9f46327502157789c5",
        "reason": "n_annotations_lt_10:7"
      },
      {
        "canonical_id": "a06ae57243ee59e6deb81f7a0447b6e630b1b91bf36ea1fb1d79672f4c890921",
        "reason": "no_table_region"
      },
      {
        "canonical_id": "3542776820ea8aa6191b0799e63e5860cc19c8631ec2345814ccc606047f09ed",
        "reason": "no_table_region"
      },
      {
        "canonical_id": "252d2eae79c33dea32d440b21023836e3294559451c04fad68db09baacf683ad",
        "reason": "no_annotations"
      },
      {
        "canonical_id": "447b8d2d90df9ff71824c7aa16adf4af33de9de1ea242d62b69dbded3b6855d4",
        "reason": "n_annotations_lt_10:8"
      },
      {
        "canonical_id": "98687e2b68676e9fb6a6f3cbeed15f5dae02ad96c487beaea14aff7d7957c521",
        "reason": "no_table_region"
      }
    ],
    "n_after_exclusion": 10678,
    "n_eligible_before_exclusion": 10679,
    "n_rows_total": 69375,
    "n_shards_scanned": 72,
    "per_shard_rows": [
      {
        "n_rows": 964,
        "shard_key": "data/train-00000-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00001-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00002-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00003-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00004-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00005-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00006-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00007-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00008-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00009-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00010-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00011-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00012-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00013-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00014-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00015-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00016-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00017-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00018-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00019-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00020-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00021-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00022-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00023-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00024-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00025-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00026-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00027-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00028-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00029-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00030-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00031-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00032-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00033-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00034-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00035-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00036-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00037-of-00072.parquet"
      },
      {
        "n_rows": 964,
        "shard_key": "data/train-00038-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00039-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00040-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00041-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00042-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00043-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00044-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00045-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00046-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00047-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00048-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00049-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00050-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00051-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00052-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00053-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00054-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00055-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00056-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00057-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00058-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00059-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00060-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00061-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00062-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00063-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00064-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00065-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00066-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00067-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00068-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00069-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00070-of-00072.parquet"
      },
      {
        "n_rows": 963,
        "shard_key": "data/train-00071-of-00072.parquet"
      }
    ],
    "revision": "0daf93102e2efce76c3e11a274a5e0d0969391d3",
    "source": "huggingface_datasets_parquet",
    "split": "train"
  },
  "federal_register": {
    "per_stratum": {
      "Notice": {
        "api_enum": "NOTICE",
        "eligibility_evaluation": "eager_full_materialization",
        "ineligible_head": [],
        "n_eligible": 940,
        "n_list_rows": 10000,
        "stratum": "Notice"
      },
      "Proposed Rule": {
        "api_enum": "PRORULE",
        "eligibility_evaluation": "eager_full_materialization",
        "ineligible_head": [],
        "n_eligible": 148,
        "n_list_rows": 1498,
        "stratum": "Proposed Rule"
      },
      "Rule": {
        "api_enum": "RULE",
        "eligibility_evaluation": "eager_full_materialization",
        "ineligible_head": [],
        "n_eligible": 250,
        "n_list_rows": 2441,
        "stratum": "Rule"
      }
    },
    "per_type_counts": {
      "Notice": 10000,
      "Proposed Rule": 1498,
      "Rule": 2441
    },
    "publication_date_gte": "2025-01-01",
    "publication_date_lte": "2025-12-31",
    "quota_by_stratum": {
      "Notice": 2,
      "Proposed Rule": 2,
      "Rule": 2
    },
    "source": "federal_register_api_v1"
  },
  "pmc_oa": {
    "dev_assignment_count_before_metadata_eligibility": 911944,
    "eligibility_evaluation": "lazy_deterministic_rank_walk",
    "eligible_records_found_before_stop": 12,
    "excluded_before_rank_walk": 0,
    "ineligible_head": [
      {
        "canonical_id": "PMC11441047.1",
        "reason": "license_not_CC_BY:CC BY-NC"
      },
      {
        "canonical_id": "PMC4231923.1",
        "reason": "license_not_CC_BY:null"
      },
      {
        "canonical_id": "PMC8885366.1",
        "reason": "license_not_CC_BY:CC BY-NC-ND"
      },
      {
        "canonical_id": "PMC11232747.1",
        "reason": "license_not_CC_BY:CC BY-NC-SA"
      },
      {
        "canonical_id": "PMC13331649.1",
        "reason": "license_not_CC_BY:CC BY-NC"
      },
      {
        "canonical_id": "PMC2665039.1",
        "reason": "license_not_CC_BY:TDM"
      }
    ],
    "metadata_records_probed": 18,
    "population_distinct_pmcids_after_latest_version_resolution": 9129850,
    "population_snapshot_total_metadata_records": 9284625,
    "rank_walk_stopping_rule": "stop after 12 eligible DEV candidates",
    "snapshot_identity": {
      "manifest_bytes_len": 1221,
      "manifest_sha256": "d7bbf63494fddd134d72c2aa4d3c9ca370be6787f1bfe021a38f06b06e32cd67",
      "manifest_url": "https://pmc-oa-opendata.s3.amazonaws.com/inventory-reports/pmc-oa-opendata/metadata/2026-09-01T01-00Z/manifest.json",
      "shards": [
        {
          "key": "inventory-reports/pmc-oa-opendata/metadata/data/49c6aee8-c648-45be-b569-79df5878fc20.csv.gz",
          "md5_from_manifest": "dff696f4b1567015afebb25e9f2cc7b7",
          "sha256_computed": "77e4e2fb9613ab6d4de11fb161cd20783f811d743972f3ae13ffecbb42f9dd70",
          "size": 54365551
        },
        {
          "key": "inventory-reports/pmc-oa-opendata/metadata/data/8c0d9b16-1216-4159-8ef3-ce544d9337a3.csv.gz",
          "md5_from_manifest": "ec16b131b270986064808ae528659acb",
          "sha256_computed": "ac814f1bf9214a4eb6cb4497930596123c6a6f666ed1cff29d7bbf8785683e91",
          "size": 80163926
        },
        {
          "key": "inventory-reports/pmc-oa-opendata/metadata/data/57d6adc1-d0f6-4ad2-8cff-ff7202e1d449.csv.gz",
          "md5_from_manifest": "1a2562efd73b697898593d3f221ee9b2",
          "sha256_computed": "8c4c809bf627f44328ce1a700afbe14cbd5183af17a837fa2d5afaaac0484013",
          "size": 6901466
        },
        {
          "key": "inventory-reports/pmc-oa-opendata/metadata/data/4e322d64-efba-4b65-a9ad-1aca0ec0936b.csv.gz",
          "md5_from_manifest": "36042713554d9a9e0f3fc68cff0e85c1",
          "sha256_computed": "e5b43654ba3c37288bc76e5912870a81334ad717c001092e25fffd7a8f6d6734",
          "size": 79273543
        },
        {
          "key": "inventory-reports/pmc-oa-opendata/metadata/data/5825c0aa-1b74-4ad2-b8dd-5379514240ea.csv.gz",
          "md5_from_manifest": "49e70201eb12ff9899cdf07f9f237b87",
          "sha256_computed": "59e583c3a439651034e81e8eb5ab30b339512f6a6395269cac05358564ea252e",
          "size": 26627627
        }
      ],
      "snapshot_utc": "2026-09-01T01-00Z"
    },
    "source": "pmc-oa-opendata inventory"
  },
  "selection_algorithm_version": "1",
  "written_utc": "2026-09-15T15:08:10+00:00"
}
```

## Exclusion summary

```json
{
  "n_entries_doclaynet": 222,
  "n_entries_federal_register": 1,
  "n_entries_pmc_oa": 2,
  "sources": {
    "doclaynet": "docs\\evaluation\\DOCLAYNET_EXCLUSION_LEDGER.jsonl",
    "federal_register": "docs\\evaluation\\FEDERAL_REGISTER_EXCLUSION_LEDGER.jsonl",
    "pmc_oa": "docs\\evaluation\\PMC_OA_EXCLUSION_LEDGER.jsonl"
  },
  "union_ledger_path": "docs\\evaluation\\DEV_PILOT_EXCLUSION_UNION.jsonl",
  "union_ledger_sha256": "345630bf8624f7c4a7616fa74def61a924e1d5954a51de389953c1a5018ed082"
}
```

## Per-corpus counts (eager-materialization corpora)

The columns below have honest population semantics only for corpora whose eligibility was evaluated eagerly (the full population was materialized and filtered). Lazy-walk corpora are reported separately immediately after this table.

| Corpus | Enumerated | Eligible | After exclusion | In DEV | Selected |
|---|---:|---:|---:|---:|---:|
| doclaynet | 69375 | 10678 | 10678 | 1057 | 6 |
| federal_register | 13939 | 1338 | 1338 | 1338 | 6 |

### Per-corpus counts (lazy-evaluation corpora)

For lazy-walk corpora, the full eligible-DEV population size is NOT MATERIALIZED. Selection stops after materializing enough eligible DEV candidates to cover the target N plus audit neighbors.

#### pmc_oa

- eligibility evaluation: `lazy_deterministic_rank_walk`
- frozen population (enumerated): `9129850`
- metadata records probed: `18`
- eligible DEV records materialized before stop: `12`
- selected: `8`
- audit neighbors: `4`
- full eligible DEV population size: **NOT MATERIALIZED**

### Federal Register per-stratum breakdown

| Stratum | Eligible | After exclusion | In DEV | Selected | Target |
|---|---:|---:|---:|---:|---:|
| Rule | 250 | 250 | 250 | 2 | 2 |
| Proposed Rule | 148 | 148 | 148 | 2 | 2 |
| Notice | 940 | 940 | 940 | 2 | 2 |

## Selected 20

| Corpus | Rank | Canonical ID | Partition hash |
|---|---:|---|---|
| pmc_oa | 0 | `PMC3569185.1` | `00001092ae4713d3...` |
| pmc_oa | 1 | `PMC4160324.1` | `00005592a35d36b7...` |
| pmc_oa | 2 | `PMC12303665.1` | `000098abfa0a0c6b...` |
| pmc_oa | 3 | `PMC10454006.1` | `0000bcfd68d509cf...` |
| pmc_oa | 4 | `PMC7773825.1` | `0000db53e646733f...` |
| pmc_oa | 5 | `PMC6839998.1` | `0000dedd5c483309...` |
| pmc_oa | 6 | `PMC5773191.1` | `0000e5cdf0c3c2e0...` |
| pmc_oa | 7 | `PMC6374309.1` | `0000e619b6ac94b1...` |
| doclaynet | 0 | `3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77` | `0005408629ca284a...` |
| doclaynet | 1 | `601febfd3972ff8015d3f121d6716a9b915533f309883a8591d8c2ced0e2c5b8` | `00580672c27b35c1...` |
| doclaynet | 2 | `664f52bb603e7d3d399c1d87a5b7cae0579387ebf1e18d675e741560ddff26ab` | `0058236af749378d...` |
| doclaynet | 3 | `8731e4b062bc18699744f25608ab9b37a37e84ffcdc6737d8bb169a88d13beda` | `007ea603cd0bd63c...` |
| doclaynet | 4 | `a1652ae40baffccaf9a56e953f103666dfe577269add0af79c2732ce72aee290` | `00a11651b36d1eb2...` |
| doclaynet | 5 | `7d2023bb74ead6e1e50639a63255ba4729f73248ee1e4254f7c227e486db88ed` | `01131c611eb0cad0...` |
| federal_register (Rule) | 0 | `2025-07879` | `008285e906e1dce3...` |
| federal_register (Rule) | 1 | `2025-13505` | `008ec9d7926fa825...` |
| federal_register (Proposed Rule) | 0 | `2025-11271` | `00a99dc75dc59824...` |
| federal_register (Proposed Rule) | 1 | `2025-16333` | `02ec1e35c2f26793...` |
| federal_register (Notice) | 0 | `2025-19924` | `00105b2bcad844d8...` |
| federal_register (Notice) | 1 | `2025-12869` | `00539e0765ff2624...` |

## Cutoff-neighbor audit

For each corpus (and each FR stratum), the candidates immediately around the selection cutoff. Rows with `selected=True` were taken; rows with `selected=False` sit just outside the cutoff. This proves no manual swap occurred.

```json
{
  "doclaynet": [
    {
      "canonical_id": "a1652ae40baffccaf9a56e953f103666dfe577269add0af79c2732ce72aee290",
      "partition_hash_hex": "00a11651b36d1eb2c3830ba05b1f19dfa4ecef47cb0087493456b3f903aabaeb",
      "rank": 4,
      "selected": true
    },
    {
      "canonical_id": "7d2023bb74ead6e1e50639a63255ba4729f73248ee1e4254f7c227e486db88ed",
      "partition_hash_hex": "01131c611eb0cad0a7a86c0d58be48934f37710cbfaead2df06327c2d6bc714f",
      "rank": 5,
      "selected": true
    },
    {
      "canonical_id": "322eb57b7e1c8b64f63545daab16219240a20ea7b458b72b94d3782eb11beb75",
      "partition_hash_hex": "012d637c5e44316dde37402ef6a392cd66ee5c0303b02bece8c270e68f8540c0",
      "rank": 6,
      "selected": false
    },
    {
      "canonical_id": "db57da05f8918ce499bcb26d56a8ca43fb5ea2a475ef65af7a70cf0468dabd8b",
      "partition_hash_hex": "013f8e27c3ee9bf5b8ab4bb072c3846afbe863df438a78e31e1363007d330fe6",
      "rank": 7,
      "selected": false
    },
    {
      "canonical_id": "fd8fdc2f9e944610073acdcf565adc54cc5011ac2a9430e52cb7cd77ee0bcd99",
      "partition_hash_hex": "018100f9e196f33260751089259092e72b0216d3a6d0f8fc0d322ef89fb7f573",
      "rank": 8,
      "selected": false
    },
    {
      "canonical_id": "e0a77f70ce1ca5736f40a35a09eb3321db335a872a10e2cfff12c68dde0c7900",
      "partition_hash_hex": "018be981ccbcde8f7cd17bbb05148362bcbfa609fedf502b0cb547ed9af568c9",
      "rank": 9,
      "selected": false
    },
    {
      "canonical_id": "39635975ff6a04b33e0adcafbc3481494a54a79f6cc66bb3d58edf4fbc9c3500",
      "partition_hash_hex": "01d694466c1d5212d0f211824cfd94cb82d484a7e40040ba8bd87c5d55cadfff",
      "rank": 10,
      "selected": false
    }
  ],
  "federal_register": [
    {
      "canonical_id": "2025-07879",
      "partition_hash_hex": "008285e906e1dce357a381359f9db231ee986f5505a1b7fa6f8cb945d90c9f83",
      "rank": 0,
      "selected": true,
      "stratum": "Rule"
    },
    {
      "canonical_id": "2025-13505",
      "partition_hash_hex": "008ec9d7926fa825a149fe6841078fcfa2a2f317fdeadde3ae94f004255956c4",
      "rank": 1,
      "selected": true,
      "stratum": "Rule"
    },
    {
      "canonical_id": "2025-13281",
      "partition_hash_hex": "00d3b910b4e52dcf3ec337843bd9b8fdb1cc60909885e7db706edd1c25da4928",
      "rank": 2,
      "selected": false,
      "stratum": "Rule"
    },
    {
      "canonical_id": "2025-17870",
      "partition_hash_hex": "013048fea310637a9c79f80a4179ed47e3a5e0bf3f75b8dcbf6c8ee8d6f7d726",
      "rank": 3,
      "selected": false,
      "stratum": "Rule"
    },
    {
      "canonical_id": "2025-15777",
      "partition_hash_hex": "02ddf068569c76f1deeef0dec3a2ed57281b0a10a18c69e49212e31a0deec71b",
      "rank": 4,
      "selected": false,
      "stratum": "Rule"
    },
    {
      "canonical_id": "2025-12145",
      "partition_hash_hex": "03741ea34e7ff31ce6c1d1306d41b514420ae2d783996baeb518e43b3b16abdc",
      "rank": 5,
      "selected": false,
      "stratum": "Rule"
    },
    {
      "canonical_id": "2025-19917",
      "partition_hash_hex": "04595c57fbccaf2efbc246507ee2996d3fe8aeddb33e828a20e4b57d5d010fe5",
      "rank": 6,
      "selected": false,
      "stratum": "Rule"
    },
    {
      "canonical_id": "2025-11271",
      "partition_hash_hex": "00a99dc75dc59824fbf120e5a3631443beb177c565783f11c4ebcfa57b199504",
      "rank": 0,
      "selected": true,
      "stratum": "Proposed Rule"
    },
    {
      "canonical_id": "2025-16333",
      "partition_hash_hex": "02ec1e35c2f26793d452dbbe0936dc186a4b322ab9fab02264b26244d0540a56",
      "rank": 1,
      "selected": true,
      "stratum": "Proposed Rule"
    },
    {
      "canonical_id": "2025-18738",
      "partition_hash_hex": "038d3a1e0a3a51f2205fdf85d9c6bbb3b894f4bbbb7c42f3f8f66638bc30f280",
      "rank": 2,
      "selected": false,
      "stratum": "Proposed Rule"
    },
    {
      "canonical_id": "2025-04986",
      "partition_hash_hex": "039e24424a4b95ce97a67ca397304c8b4949ce476985eb8584a3e5a2a3ce7a48",
      "rank": 3,
      "selected": false,
      "stratum": "Proposed Rule"
    },
    {
      "canonical_id": "2025-12032",
      "partition_hash_hex": "03cd837a43447d756205e69a56b93b110f6b785d9ccf138e97704d5068f24db3",
      "rank": 4,
      "selected": false,
      "stratum": "Proposed Rule"
    },
    {
      "canonical_id": "2025-14687",
      "partition_hash_hex": "0c4a46ef5ea1aa042fc39d662ef8b3e97a6bdccb6bed222d9eea96003cd79930",
      "rank": 5,
      "selected": false,
      "stratum": "Proposed Rule"
    },
    {
      "canonical_id": "2025-01458",
      "partition_hash_hex": "0cf1889b10a71ccc3c7f6cd4dae3af6b7efeb792b92de8f379975b89a056e4d3",
      "rank": 6,
      "selected": false,
      "stratum": "Proposed Rule"
    },
    {
      "canonical_id": "2025-19924",
      "partition_hash_hex": "00105b2bcad844d87980e80c93acff11f8227f0f4a57e744f5ebecad7a2a1b2d",
      "rank": 0,
      "selected": true,
      "stratum": "Notice"
    },
    {
      "canonical_id": "2025-12869",
      "partition_hash_hex": "00539e0765ff262492629e23e22a696f5a69dc56de6b31505ffcf3b0170458c7",
      "rank": 1,
      "selected": true,
      "stratum": "Notice"
    },
    {
      "canonical_id": "2025-19863",
      "partition_hash_hex": "00860fa9d17f8fa1aebad4e7d63853da5109d64ecfac73b2ace11f86e3364573",
      "rank": 2,
      "selected": false,
      "stratum": "Notice"
    },
    {
      "canonical_id": "2025-21997",
      "partition_hash_hex": "00fdaf7507d10ce065b3159bacc2c44f70d821e7c6334751e7865f25dfe7c6be",
      "rank": 3,
      "selected": false,
      "stratum": "Notice"
    },
    {
      "canonical_id": "2025-13273",
      "partition_hash_hex": "011191fa3745978343222446122aad4b7dc2ab3a353318c85ea6840c28bee381",
      "rank": 4,
      "selected": false,
      "stratum": "Notice"
    },
    {
      "canonical_id": "2025-16441",
      "partition_hash_hex": "01147c89755032968bcfab460fb76a1a620741bdbe96536430c6b0a358c09f5e",
      "rank": 5,
      "selected": false,
      "stratum": "Notice"
    },
    {
      "canonical_id": "2025-19774",
      "partition_hash_hex": "01242d6911c0d0e0341ef61215634abc25ef24a07cc12efcfc328e4423edb447",
      "rank": 6,
      "selected": false,
      "stratum": "Notice"
    }
  ],
  "pmc_oa": [
    {
      "canonical_id": "PMC5773191.1",
      "partition_hash_hex": "0000e5cdf0c3c2e076c65cb36bdfa481205290a572e7dbf0508c637e43718ffb",
      "rank": 6,
      "selected": true
    },
    {
      "canonical_id": "PMC6374309.1",
      "partition_hash_hex": "0000e619b6ac94b1e5d3fc41385d1fa252a7e3d5f73796bc852963616b8b1e22",
      "rank": 7,
      "selected": true
    },
    {
      "canonical_id": "PMC8381808.1",
      "partition_hash_hex": "0000f8b6a74e604812340b2e34ba12a2e34dda133f0940cd41c830653a0b4a88",
      "rank": 8,
      "selected": false
    },
    {
      "canonical_id": "PMC3544647.1",
      "partition_hash_hex": "000103036c655aa1c5bd3bbf4f3a2d130c5da7f967306f4b4ae4c468bdb0e079",
      "rank": 9,
      "selected": false
    },
    {
      "canonical_id": "PMC3328247.1",
      "partition_hash_hex": "00014c9e2fe48b5176034f5024ddbe72fdffc48882d9d737e646d2b2f7839c6b",
      "rank": 10,
      "selected": false
    },
    {
      "canonical_id": "PMC7256328.1",
      "partition_hash_hex": "000151b7c7eee74cbb51bebf8dc3ae7c2f799083ce1d4c6675c305221402a4e0",
      "rank": 11,
      "selected": false
    }
  ]
}
```
