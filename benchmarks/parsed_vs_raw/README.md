# parsed-vs-raw evaluation harness

This harness answers the question Doc 2 posed for the AksharaMD product:
does the readiness score AksharaMD emits for a candidate extraction
correlate with how well a downstream LLM answers grounded questions
about the source document?

## What the experiment measures

For each `(document, question)` pair drawn from a QA-grounded corpus we
run three arms:

- **Arm A - raw**: the LLM answers directly from the source PDF via
  Anthropic's `document` content block. No extraction step, so no
  readiness score. This is the "no extraction" baseline.
- **Arm B - parser X**: the LLM answers from parser X's markdown
  extraction of the same PDF. We record the parser's markdown, the
  AksharaMD readiness score for that extraction, and the LLM's answer.
- **Arm C - parser Y**: same as B but with a different parser.

An LLM-as-judge (reused from `benchmarks/llm_qa_eval.py`) scores each
answer 0-10 against the gold answer. We aggregate per-arm mean
correctness and, crucially, the Pearson and Spearman correlations
between the readiness score and correctness for the parser arms.

**A positive Spearman on the parser arms is the evidence Doc 2's
proposal predicts.** If the score is a useful proxy for downstream
answer quality, low-scoring extractions should produce more wrong
answers than high-scoring ones. If the correlation is near zero the
score is not a useful proxy on that corpus.

## Supported arms

| arm        | dependency        | readiness_score source                                    |
|------------|-------------------|------------------------------------------------------------|
| raw        | anthropic         | n/a (no extraction step)                                   |
| aksharamd  | (bundled)         | `Compiler.compile_to_string` -> `ctx.manifest.readiness_score` |
| markitdown | `pip install markitdown` | Assessor proxy from source + candidate bytes         |
| docling    | `pip install docling`    | Assessor proxy from source + candidate bytes         |

The AksharaMD arm's readiness score is the exact value the Compiler
computes for its own pipeline. External parsers cannot reproduce the
same Compiler-internal state, so their readiness score is derived from
the `aksharamd.assessment.Assessor` verdicts against source + candidate
bytes: PASS on every dimension yields 100; each CONCERN drops 15 points;
each FAIL drops 30; each UNDETERMINED drops 5. This is a documented
proxy, not the raw Compiler score, and both numbers should be treated
as different measurement instruments when interpreting cross-parser
comparisons.

## Supported corpora

- **QASPER** (`--corpus qasper`): 1,585 arXiv NLP papers, 5,049 grounded
  QA pairs (extractive/abstractive/boolean/unanswerable). Loaded via
  `datasets.load_dataset("allenai/qasper", split="validation")`. Source
  PDFs are fetched from arxiv.org and cached under `.cache/qasper/`.

## Cost estimates

- Pilot (`--limit 5 --questions-per-doc 3 --arms raw,markitdown,aksharamd`):
  45 answer calls + 45 judge calls on Haiku ~ **$1-3**.
- Serious run (100 docs x 5 questions x 3 arms = 1,500 answer + 1,500
  judge calls, Haiku answer, Sonnet judge): **$50-100** depending on
  average document length.

Raw-arm calls pay for the PDF bytes in the input tokens, so the raw arm
is the most expensive per call. Trimming very large papers or capping
input tokens is the main dial for cost control.

## Running the pilot

Prerequisites:

```
pip install "aksharamd[eval]"
export ANTHROPIC_API_KEY=sk-...
```

Single command:

```
python -m benchmarks.parsed_vs_raw.run \
    --corpus qasper \
    --limit 5 \
    --questions-per-doc 3 \
    --arms raw,markitdown,aksharamd \
    --answer-model claude-haiku-4-5-20251001 \
    --judge-model claude-haiku-4-5-20251001 \
    --output benchmarks/results/parsed-vs-raw-qasper-pilot/
```

Output:

```
benchmarks/results/parsed-vs-raw-qasper-pilot/
    rows.csv        # one row per (doc, question, arm)
    summary.md      # per-arm means + correlations
    summary.json    # same, machine-readable
```

## Running without an API key

Two options:

- `--dry-run`: performs all extractions and computes readiness scores,
  skips every LLM call, writes extractions under `dry_run/<arm>/`.
  Useful for auditing what would be sent.
- `--fixture-mode <path>`: uses a JSON fixture of canned LLM responses
  (see `benchmarks/parsed_vs_raw/fixtures/llm_responses.json` for the
  shape). This is what the test suite exercises.

## Interpreting the output

`summary.md` reports for each arm:

- `n`, `n_scored`: total rows and rows where the judge returned a valid
  score.
- `mean_correctness`: mean of the normalized 0-1 correctness values.
- `mean_readiness`: mean readiness score across the parser arm's rows.
- `pearson`, `spearman`: correlations between readiness_score and
  correctness for that arm.

Reading:

- **Positive Spearman (e.g. > 0.3)**: the score predicts downstream
  quality. This is the product claim in Doc 2's proposal.
- **Near-zero Spearman**: the score is not tracking answer quality on
  this corpus. Motivates revisiting the scoring policy or the corpus
  choice.
- **Negative Spearman**: the score is anti-correlated with quality on
  this corpus. Very strong signal that something is wrong.

## Scaffold vs. real result

This module is a scaffold. Every test runs offline; no real Anthropic
call is issued. To produce the actual correlation number requires an
`ANTHROPIC_API_KEY` and a live network. The next step after the pilot
is a larger `--limit 100`-scale run with a Sonnet judge.
