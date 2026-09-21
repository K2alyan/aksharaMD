# Track C V2 source/candidate diagnostics

Status: **exploratory; not part of the frozen V1 endpoint**.

This local-only tool reuses the saved Track C parser Markdown and cached QA
metrics. It makes no model or network calls and never writes a
`track_c_result.json`. Source/candidate evidence is written under a separate V2
root using the approved `source-candidate-preservation-v2-exploratory` API.

## Run safely

Validate all inputs before writing:

```powershell
python -m benchmarks.eval_v1.stage2.track_c_v2_diagnostics `
  --run-dir benchmarks/results/stage1-track-c-2026-09-17 `
  --cache-root .cache `
  --output-dir benchmarks/results/stage2-track-c-v2-source-candidate `
  --dry-run
```

Then omit `--dry-run` to write:

- one `track_c_v2_source_candidate.json` sidecar per eligible V1 record under
  `OUTPUT/sidecars/<corpus>/<document>/<parser>/`; and
- `OUTPUT/track_c_v2_diagnostics.json`.

The report contains the relative path and SHA-256 of every sidecar in its
evidence inventory. Files not named by that inventory are not report evidence;
a partial execution without a final report is not a completed diagnostic run.

Use a new, absent or empty output directory for every run; dry-run verifies
this destination too. Output may not overlap either V1 inputs or the source
cache. The command has no API
client, does not read an API key, and does not execute a parser or QA model.

The required real V1 inventory is exactly 245 records: 239 executable records
and the six pinned frozen `DEFECT` identities. Each defect's result identity,
source, null evaluation state, execution receipt, empty output, manifest hash,
and parser-contract hash are verified before it is accepted as ineligible.
Missing, extra, or substituted records fail the inventory gate. Declared
defects appear as `v1_ineligible` exclusions;
identity, hash, provenance, or assessment failures are fatal and produce exit
code 2. The report remains explicit about every exclusion.

## Evidence contract

The tool verifies before reuse:

- V1 result, metric, prompt, model, and scoring-contract versions;
- complete successful QA rows, prediction hashes, and recomputed aggregate
  cached metrics;
- candidate identity against both the V1 Phase 2 input hash and, for real
  parser arms, the Phase 1 logical-output hash and byte count;
- execution receipt identity and contract hashes; and
- the exact current source and candidate bytes passed to the V2 API.

`corpus_gold` is an annotation-derived virtual arm and therefore has no Phase 1
execution receipt. It is assessed and reported, but excluded from parser
selection diagnostics.

V1 did not record source-PDF hashes in per-pair execution receipts. The V2
sidecar therefore binds the exact canonical cache PDF resolved at execution
time and states this retrospective provenance limitation. It must not be
described as a cryptographic proof that these bytes were the Phase 1 bytes.

## Diagnostic definitions

The source/candidate contract intentionally provides no combined scalar. These
diagnostics rank only by the activated
`source.pdf_text_token_retention` detector score and report all other detector
activation, eligibility, verdict, score, and abstention evidence separately.

- **Tie rate:** fraction of within-document parser pairs with equal text
  retention scores.
- **Pairwise concordance:** among non-tied evidence and QA pairs, the fraction
  where the higher text-retention score also has the higher cached primary QA
  score. Confidence intervals resample documents, not parser pairs.
- **Top-one regret:** document-oracle cached primary QA score minus the score
  of the parser selected by maximum text retention. Parser ID breaks ties
  deterministically.
- **Top-one accuracy:** fraction of documents where that selected parser ties
  for the best cached primary QA score.
- **Best fixed:** lowest mean regret among parsers available for every included
  document. This is an in-sample descriptive baseline, not a deployment
  estimate.
- **Random:** expected regret from a uniform choice among assessed parser arms
  available for that document.
- **False accept:** an accepted output whose cached primary score is at least
  `--bad-regret-margin` below the best assessed parser for that document.
- **Risk/coverage:** mean oracle regret and accepted fraction at each requested
  text-retention threshold. Documents need at least two scorable parser arms
  for these comparative quantities. Coverage, risk, false-accept rate,
  concordance, selection regret, and selection accuracy carry deterministic
  document-bootstrap intervals when there is enough evidence.

Results are emitted pooled, by corpus, and by corpus/parser. QASPER token F1
and TAT-DQA numeric EM must not be interpreted as a single homogeneous metric;
pooled summaries are descriptive and the per-corpus results are primary for
interpretation.
