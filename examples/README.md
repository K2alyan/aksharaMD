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

Demonstrates a readiness gate pattern for RAG ingestion pipelines:

- Compiles a set of documents (generated in-place — no external files needed).
- Scores each document against a configurable threshold (`MIN_READINESS_SCORE`).
- Documents that meet the threshold proceed to a mock embed step.
- Documents that fall below the threshold are blocked before embedding.
- Prints the full structured result for one document, mirroring the output
  of `aksharamd compile --json`.

**CLI equivalents:**

```bash
# Get structured JSON output for one document
aksharamd compile doc.pdf --json

# Block ingestion if readiness score is below 70
aksharamd compile doc.pdf --json --min-readiness-score 70
echo "exit code: $?"
```

**When to use this pattern:**

Use a readiness gate when building automated ingestion pipelines where
low-quality extractions (garbled OCR, near-empty output, encrypted PDFs)
must not reach a vector store.  Set the threshold based on your corpus:
HIGH (≥85) for production ingestion, OK (≥70) for internal search.

See [ADR-10](../ADR.md#adr-10----min-readiness-score-ingestion-gate) for the
design rationale behind the `--min-readiness-score` flag.
