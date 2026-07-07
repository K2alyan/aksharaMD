# Readiness Gate Benchmark

Measures whether the AksharaMD readiness score gate improves retrieval quality by keeping
low-information-density documents out of a RAG index.

## What it does

Two ingestion policies are compared on a fixed 10-question set:

| Policy | Threshold | Behaviour |
|--------|-----------|-----------|
| A — baseline | 0 | All compiled documents enter the index regardless of readiness score. |
| B — gated | 94 | Only documents scoring ≥ 94 are indexed. Lower-scoring documents are held for review. |

Retrieval uses TF-IDF cosine similarity at **top-k = 1** (strict: the answer must appear in the
single best-matching chunk). No external dependencies are required.

## Corpus

Six documents in `corpus/`:

| File | Type | Score | Role |
|------|------|-------|------|
| `01_employee_handbook.md` | Markdown | 95 | Clean — specific HR facts (leave days, office address, review schedule, premium %) |
| `02_api_documentation.md` | Markdown | 95 | Clean — specific API facts (timeout, rate limit, token expiry, page size) |
| `03_product_changelog.md` | Markdown | 95 | Clean — specific release facts (dates, deprecated endpoints) |
| `04_hr_boilerplate.txt` | Plain text | 93 | Noisy — generic HR language, dense HR query terms, no specific values |
| `05_generic_api_notes.txt` | Plain text | 93 | Noisy — generic API notes, dense API query terms, no specific values |
| `06_placeholder.txt` | Plain text | 93 | Noisy — placeholder draft text |

The clean documents (score 95) contain the specific values that answer each question.
The noisy documents (score 93) share query-relevant vocabulary but contain no specific answers,
so they compete for ranking without contributing useful content.

**Policy B threshold = 94** exploits the natural format-based score gap: AksharaMD scores
Markdown at 95 and plain text at 93 by default. All six documents are valid extractions
(band HIGH, no errors); the gate separates them on information density, not extraction quality.

## Running the benchmark

```bash
python -m benchmarks.readiness_gate.run_benchmark
# or
python benchmarks/readiness_gate/run_benchmark.py
```

Requires `aksharamd` to be installed (`pip install aksharamd`).

## Typical results

On this purpose-built six-document corpus at top-k=1:

```
Policy                        Indexed    Held    Hits   Hit rate
----------------------------------------------------------------
A — no gate (threshold=0)           6       0    6/10       60%
B — gated (threshold=94)            3       3   10/10      100%
================================================================

Gating effect: +40% improvement in retrieval hit rate @ top-1
```

On this corpus, Policy A misses four questions because a noisy chunk (high term density,
no answer value) ranks above the correct answer chunk. Policy B removes the noisy documents
from the index, so the correct chunk ranks first for every query. These numbers are specific
to this corpus and retrieval method; see Limitations.

## Methodology

**TF-IDF retrieval.** Each document is split into chunks by the AksharaMD compiler. A
TF-IDF index is built from all chunks in the active policy. Retrieval scores each chunk
against the query using cosine similarity. The answer is considered found if any of the
top-k chunks contains all answer keywords as substrings (case-insensitive).

**Strict retrieval depth (top-k = 1).** The benchmark uses k = 1 to amplify the gate's
effect. At this depth a single noisy chunk ranking first is sufficient to cause a miss.
At k = 3 or higher, the correct chunk often remains in the result set even when noisy
chunks rank above it, which can mask the improvement. Real RAG pipelines typically use
k = 3–10; the gate's practical benefit at those depths depends on corpus characteristics.

**No embedding model.** This benchmark uses lexical retrieval only. Embedding-based
retrieval handles synonymy and paraphrase differently. A gated corpus can still improve
embedding retrieval when noisy documents introduce topically similar but semantically
shallow content, but the magnitude of the effect may differ.

## Limitations

- **Illustrative corpus.** The six documents and ten questions are purpose-built to
  demonstrate the gate's effect under lexical retrieval. Results are not representative
  of production corpora.
- **Format proxy.** The score gap in this benchmark is driven by file format (`.md` vs
  `.txt`), which happens to correlate with information density here. In real corpora,
  format alone is not a reliable quality signal; AksharaMD's full scoring considers
  structure, completeness, and warning codes alongside format.
- **No embedding model.** Dense retrieval may show different sensitivity to the gate
  depending on how well the embedding model handles shallow, keyword-heavy content.
- **Small scale.** Ten questions and six documents are too few to draw statistical
  conclusions. This benchmark is a functional demonstration, not a research study.

## Relation to the AksharaMD readiness score

The readiness score is not a measure of extraction quality — AksharaMD produces a HIGH-band
extraction for all six documents in this corpus. The score reflects the document's
information density and structural completeness. The gate in Policy B is a **pipeline
policy decision**: index documents that meet the information-density threshold, and route
lower-scoring documents to a manual review queue for enrichment or removal.

See `examples/05_readiness_gate_demo.py` for an interactive walkthrough of two-policy
gating using the AksharaMD Python API.
