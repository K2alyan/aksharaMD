# OCR Auto Policy v1 — Calibration Harness

Evidence-only harness that runs corpus documents through three OCR treatments
(`tesseract`, `unlimited_ocr`, `auto`) and records readiness, runtime, VRAM,
repetition, and structural metrics. **Never** changes production policy
thresholds; produces JSON + Markdown + review queue artifacts under
`results/<date>_schema_v<n>/`.

## Layout

```
benchmarks/ocr_auto_calibration/
├── README.md                    — this file
├── schema.py                    — typed RunKey / RunResult / DocumentSummary / RunReport
├── corpus.py                    — ParseBench + synthetic + failure + local enumeration
├── synthetics.py                — deterministic-recipe PDF generator (8 profiles)
├── metrics.py                   — repetition detection, GFM structure counts, provenance
├── preference.py                — layered auto / human / final preference labeller
├── review_queue.py              — human-review queue generator
├── cache.py                     — content-addressable result cache
├── harness.py                   — orchestrator: subprocess, timeouts, VRAM sampling
├── report.py                    — Markdown emitter
├── run.py                       — CLI entry point
├── fixtures/
│   ├── synthetic/               — *gitignored* — regenerate with `python -m benchmarks.ocr_auto_calibration.synthetics`
│   ├── local/                   — optional user-supplied real fixtures with sibling .json labels
│   └── failure/                 — real-first failure fixtures
├── .cache/                      — result cache (gitignored)
└── results/                     — per-run artifacts (gitignored)
```

## Synthetic fixtures — byte determinism vs. semantic determinism

The 8 synthetic PDFs under `fixtures/synthetic/` are produced by
`synthetics.py` from a small deterministic recipe. **Their PDF bytes are
NOT stable across regenerations** — PyMuPDF injects creation/modification
timestamps and non-deterministic xref layout. Their *semantics* are stable:
page count, per-page image-only vs text-bearing classification, expected
Auto-Policy backend, image content, and label JSON all round-trip
identically.

Consequence: **synthetic PDF SHA-256 is not a valid cache identity.** The
harness keys synthetic cache entries on a stable recipe hash instead
(`CorpusEntry.stable_identity`, propagated from the sibling `.hash` file
which stores `_recipe_hash(profile)`). The actual on-disk PDF SHA-256 is
still computed and recorded in `RunResult.document_sha256` and in the
acquisition envelope, for reviewer-facing provenance only.

The semantic contract is enforced by
`tests/test_ocr_auto_calibration_synthetics.py`.

## Cache identity contract

| Entry source | Cache identity                                    | Provenance SHA recorded |
|--------------|---------------------------------------------------|-------------------------|
| parsebench   | lockfile-recorded `sha256`                        | live on-disk SHA        |
| synthetic    | `synthetic:v1:<recipe_hash>` (`.hash` file)       | live on-disk SHA        |
| failure      | live on-disk SHA                                  | live on-disk SHA        |
| local        | live on-disk SHA                                  | live on-disk SHA        |

## Usage

```bash
# Regenerate synthetic fixtures (idempotent — only rewrites on recipe change)
python -m benchmarks.ocr_auto_calibration.synthetics

# Dry-run (no subprocess, no GPU) — smoke-tests report/queue emission
python -m benchmarks.ocr_auto_calibration.run --dry-run --resume

# Real run against a subset
python -m benchmarks.ocr_auto_calibration.run --corpus synthetic --resume
```
