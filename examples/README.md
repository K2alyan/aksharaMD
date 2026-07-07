# Examples

Runnable scripts showing common AksharaMD usage patterns.  Each example is
self-contained and requires only `aksharamd` to be installed.

```bash
pip install aksharamd
```

---

## 01 — Compile a file

```bash
python examples/01_compile_file.py path/to/document.pdf
```

Compiles one document to Markdown and prints token counts, readiness score,
and the first 2 000 characters of output.

---

## 02 — Compile a URL

```bash
python examples/02_compile_url.py https://example.com/report.html
```

Fetches and compiles a remote document.  Requires a live network connection.

---

## 03 — Batch compile

```bash
python examples/03_batch_compile.py docs/*.pdf
```

Compiles multiple files and prints a comparison table of token counts,
reduction percentages, readiness scores, and timing.

---

## 04 — Extract and chunk

```bash
python examples/04_extract_and_chunk.py path/to/document.pdf
```

Shows how to iterate over compiled chunks and access per-chunk metadata
(tokens, confidence breakdown, source reference).

---

## 05 — Readiness gate ingestion demo

```bash
python examples/05_readiness_gate_demo.py
```

Demonstrates a policy-based readiness gate for RAG ingestion pipelines:

- Compiles a set of documents (generated in-place — no external files needed).
- Runs two policies side by side so you can see how the same documents are
  routed differently depending on the configured threshold.
- Documents that meet the threshold proceed to a mock embed step.
- Documents that do not meet the threshold are routed to a mock review queue.
  This does not mean the extraction is bad — it means the document does not
  meet the configured pipeline policy.
- Prints the full structured result for one document, mirroring the output
  of `aksharamd compile --json`.

**CLI equivalents:**

```bash
# Get structured JSON output for one document
aksharamd compile doc.pdf --json

# Route to CI failure if score is below 85 (strict production gate)
aksharamd compile doc.pdf --json --min-readiness-score 85
echo "exit code: $?"
```

**Threshold reference:**

| Threshold | Band  | Typical use                          |
|-----------|-------|--------------------------------------|
| ≥ 85      | HIGH  | Strict production ingestion          |
| ≥ 70      | OK    | Internal search / lenient ingestion  |
| < 70      | RISKY/POOR | Investigate before embedding    |

**When to use this pattern:**

Use a readiness gate when building automated ingestion pipelines where
documents that do not meet your team's quality policy must not reach a
vector store automatically.  The threshold is a pipeline policy decision —
not an absolute quality judgment.  A score of 93/HIGH is a good extraction;
whether it passes depends on what your pipeline requires.

See [ADR-10](../ADR.md#adr-10----min-readiness-score-ingestion-gate) for the
design rationale behind the `--min-readiness-score` flag.
