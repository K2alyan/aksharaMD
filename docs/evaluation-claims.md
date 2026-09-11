# Evaluation and claims policy

The product target is lower total ingestion cost at a declared information and
answer-quality target. Output-size reduction alone does not establish success.

## Separate evidence

- Extraction diagnostics identify observed risks. A heuristic score is not a
  calibrated probability; EXTRACTED/INFERRED/AMBIGUOUS describe provenance.
- Source fidelity requires source-grounded evidence about omissions, values,
  units, associations, and ordering.
- Content/task usefulness requires its own evidence. A faithful copy of a
  placeholder-filled source can preserve text without supporting a task.
- Measured savings require the complete cost of an equivalent workload.

Keep native benchmark dimensions and denominators. Do not average unrelated
benchmarks into a universal correctness percentage. Preserve old policies and
run identities for replay; label historical evidence rather than rewriting it
as a result for new code.

## Controlled comparison

Run the same source-grounded tasks through three arms:

1. Raw source delivered directly to the chosen LLM.
2. The selected parser's output delivered to that LLM.
3. The same parser followed by AksharaMD, then that LLM.

Arm 3 versus arm 2 isolates AksharaMD's contribution. Fix model configuration,
source identities, parser settings, request representations, and evaluation
rules. Keep all attempted cases, including unsupported inputs, failures,
abstentions, retries, and fallback requests.

Create questions and supporting evidence from original sources before inspecting
candidate outputs. Cover document tails, units, negation, table associations,
cross-page relationships, and unanswerable questions. Split source/template
families before tuning. Keep related pages and mutations in one split.

Report answer accuracy, unsafe acceptance among accepted outputs, acceptance
coverage, and uncertainty. Predeclare quality targets and cost comparisons.
Do not use a shorter context window in an accuracy experiment and claim measured
spending for a different full-document context.

## Cost and compute accounting

Net savings equal baseline total cost minus the complete alternative-path cost.
Include parser/OCR, assessment, provider input/output usage, retries, fallback,
and any query-reuse amortization. Record observed provider usage alongside local
token estimates; multimodal source costs cannot be inferred by tokenizing binary
file bytes or text alone. Date and identify any price schedule used.

Measure wall latency, CPU/GPU time, and throughput separately on named hardware
and workload settings. Electrical-power claims require energy measurements.
Analytical capacity and prefill models must be labeled projections.

## Historical benchmark limitations

The legacy QA runner uses a 6,000-character prefix for answers but counts the
complete conversion for output-token comparisons. Its automatic QA mode uses
AksharaMD output to create questions; missing source facts may therefore be
excluded. Not every historical reference's origin has been established.
Different tools may cover different format cohorts. These studies cannot prove
equivalent full-document preservation, actual billed savings, or hardware gains.

Publish new claims only with the exact workload, parser, model, code/configuration
identity, delivered artifacts, quality results, and measured net costs.
