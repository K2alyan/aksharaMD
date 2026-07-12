# Architecture Decision Records — AksharaMD

Each entry explains why a significant decision was made, what was considered, and what tradeoffs were accepted. Read this before opening an issue about why the system works a specific way, or before proposing a change that touches one of these areas.

---

## ADR-01 — Local-first, no cloud dependency

**Context:** Document ingestion pipelines often rely on cloud APIs (LlamaParse, Azure Document Intelligence, AWS Textract) for parsing and OCR. Users upload documents to a third-party service.

**Decision:** AksharaMD runs entirely on-device. No document leaves the machine. No API key is required for the base install. Network calls only happen when the user explicitly passes an `http://` or `s3://` URL as input.

**Alternatives considered:**
- Cloud OCR for scanned PDFs: rejected — would require an API key, add latency, and send potentially sensitive documents to a third party.
- Optional cloud-backed parsing for specific formats: rejected — the complexity of a hybrid local/cloud model was not worth the incremental quality gain on formats already handled well locally.

**Consequences:** Some formats (scanned PDFs with complex table layouts) produce lower-quality output than cloud services. The optional `[vision]` extra (Marker, local neural model) partially closes this gap without a cloud dependency.

---

## ADR-02 — 10-stage pipeline with pluggable stages

**Context:** Document parsing involves multiple distinct concerns: format detection, extraction, cleaning, quality assessment, chunking, and export. Monolithic parsers make it hard to test, swap, or skip individual concerns.

**Decision:** The pipeline is broken into 10 named stages — `detect → parse → clean → optimize → validate → chunk → tokenize → manifest → score → export` — each receiving and returning a `CompilationContext`. Stages are registered as plugins and discovered at import time.

**Alternatives considered:**
- Single-pass extraction per parser: simpler but each parser would need to reimplement cleaning, deduplication, chunking, and scoring independently.
- DAG-based pipeline (like Airflow/Prefect style): too much overhead for a single-process tool; the linear stage ordering covers all real use cases.

**Consequences:** Adding a new output format, scoring signal, or cleaning pass requires only a new plugin class with no changes to the orchestrator. The tradeoff is that `CompilationContext` becomes the shared mutable state object that all stages must agree on.

---

## ADR-03 — AI Readiness Score with named quality bands

**Context:** Every document parser returns text. None tell the caller whether that text is reliable enough to embed. A scanned PDF, garbled OCR, or near-empty extraction can look complete until the LLM gives a wrong answer.

**Decision:** Every compilation produces a 0–100 readiness score and a named band: HIGH (≥85) / OK (≥70) / RISKY (≥50) / POOR (<50). The score is computed from per-block extraction confidence (EXTRACTED / INFERRED / AMBIGUOUS) plus penalties for specific warning conditions.

**Alternatives considered:**
- Binary pass/fail: loses nuance — a 65-scoring document is usable with caveats; a 30-scoring document is not.
- Percentile ranking relative to corpus: not useful without a reference corpus. Absolute thresholds let teams set gates without collecting baseline data.
- LLM-based quality judgment: would require an API call per compilation, defeating the local-first principle.

**Consequences:** The score reflects extraction quality, not retrieval accuracy or answer correctness downstream. This is intentional and is stated explicitly in the README. Teams still need to run retrieval evals on their specific query distribution.

---

## ADR-04 — Warning-code based validation

**Context:** Quality issues in document extraction are specific and actionable — a scanned page needs OCR, a garbled font needs a different parser, an encrypted PDF cannot be opened at all. Generic error messages don't tell the caller what to do.

**Decision:** Validation produces named warning codes (`OCR_REQUIRED`, `ENCRYPTED_PDF`, `GLYPH_ARTIFACTS`, `LOW_TEXT_DENSITY`, `NEAR_EMPTY_OUTPUT`, `TOKEN_BLOAT`, `REPEATED_CONTENT`) rather than free-text messages. Each code maps to a specific condition, a recommended action, and a score penalty.

**Alternatives considered:**
- Log-level severity only (WARNING/ERROR): loses the machine-readable identity of the issue.
- HTTP-style numeric codes: less readable in CLI output and harder to act on without a lookup table.

**Consequences:** Warning codes are part of the public API surface — removing or renaming one is a breaking change for callers who gate on specific codes.

---

## ADR-05 — Schema-versioned JSON output

**Context:** Downstream consumers (vector stores, RAG pipelines, monitoring dashboards) parse the four output JSON files (`manifest.json`, `document.json`, `validation.json`, `chunks/*.json`). If field names change, callers break silently.

**Decision:** All four models carry `schema_version = "1.0"`. Callers can check this field and fail fast on an unexpected version rather than silently misparse.

**Alternatives considered:**
- URL-based versioning in a schema registry: too heavy for a local CLI tool.
- No versioning: breaks existing integrations on any field rename without warning.

**Consequences:** Any field rename, type change, or removal requires a schema version bump. Additive changes (new optional fields) do not require a bump.

---

## ADR-06 — Chunk JSON with per-document source metadata

**Context:** When chunks are embedded into a vector store, retrieval results need to be traceable back to their source document and carry quality signals so low-confidence results can be filtered.

**Decision:** Each chunk carries `source` (file path or URL), `file_type`, `tokens`, and a `confidence` breakdown (`extracted`, `inferred`, `ambiguous` block counts). This metadata is stored as JSON alongside the chunk text in `chunks/*.json`.

**Alternatives considered:**
- Source path only: loses the quality breakdown needed for retrieval filtering.
- Full document metadata in every chunk: redundant and increases chunk size.

**Consequences:** Callers can filter retrieval results by `confidence.ambiguous` count or by `file_type` without re-running the pipeline.

---

## ADR-07 — MinHash LSH for near-duplicate detection

**Context:** Corpus ingestion (`compile_corpus`) processes large document collections that often contain near-duplicate files (slightly different versions of the same report, re-saved copies). Embedding duplicates wastes tokens and distorts retrieval.

**Decision:** Near-duplicate detection uses MinHash with Locality Sensitive Hashing (LSH), using the same Mersenne-prime universal hashing family as downstream graph pipelines (e.g. Graphify). Two documents with Jaccard similarity ≥ the configured threshold (default 0.5) are treated as duplicates; the second is skipped.

**Alternatives considered:**
- Exact hash deduplication: misses near-duplicates (documents with minor edits).
- Embedding-based similarity: requires an embedding model, which violates the local-first and zero-ML-dependency principle for the base install.
- Simhash: faster but lower accuracy on short documents.

**Consequences:** The Jaccard threshold is corpus-dependent. The default of 0.5 is conservative (two documents need to share more than half their shingles to be considered duplicates). Collections with many versions of the same document may need a lower threshold.

---

## ADR-08 — Optional extras instead of heavy base install

**Context:** Full document AI capabilities (OCR, neural layout detection, audio transcription, math equation extraction) require large model downloads and ML frameworks. Most users only need a subset of these for their specific document types.

**Decision:** The base install (`pip install aksharamd`) has zero ML dependencies and handles PDFs with a text layer, all Office formats, HTML, EPUB, email, archives, and more. ML capabilities are available as optional extras: `[ocr]`, `[vision]`, `[math]`, `[audio]`, `[cloud]`, `[full]`.

**Alternatives considered:**
- Always-on ML: a ~5 GB install for capabilities most users never use.
- Separate packages per format: more complex dependency management for the caller.

**Consequences:** When a document needs a capability that is not installed, AksharaMD flags it with a named warning code and a lower readiness score rather than silently producing garbage output. Users know exactly which extra to install. The tradeoff is that users with varied document types need to know which extras apply to their corpus.

---

## ADR-09 — PolyForm Noncommercial license

**Context:** AksharaMD is open-source in the sense that the source code is publicly readable, but commercial use without a license agreement is not permitted.

**Decision:** PolyForm Noncommercial 1.0.0. Free for personal and non-commercial use. Commercial use requires a separate license.

**Alternatives considered:**
- MIT/Apache: fully permissive, but allows competitors to ship AksharaMD as the core of a commercial product without contributing back or compensating the project.
- GPL/AGPL: copyleft propagation may block legitimate enterprise use cases where the organization cannot open-source its pipeline.
- SSPL: similar to AGPL but targeted at cloud services; well-known but contentious.

**Consequences:** Commercial users need to contact the project for a license. This is a deliberate friction point — the goal is to fund continued development, not to block usage.

---

## ADR-10 — `--min-readiness-score` ingestion gate

**Context:** Teams building RAG pipelines often want to block low-quality extractions from entering a vector store automatically, rather than inspecting every compilation result manually.

**Decision:** `aksharamd compile <file> --min-readiness-score INTEGER` exits non-zero if the readiness score is below the threshold. Output files are still written (the extraction happened; the caller may still want the artifacts or the warnings). The exit code signals the CI/CD system to stop before the embed step.

**Alternatives considered:**
- Delete output on failure: loses the artifacts and warnings needed to diagnose the issue.
- Separate `gate` subcommand: unnecessary indirection; the gate is a natural modifier on the existing compile step.
- Soft warning only: does not integrate cleanly with shell pipelines and CI systems that rely on exit codes.

**Consequences:** The threshold is caller-defined. There is no universal "correct" threshold — HIGH (≥85) is appropriate for production ingestion; OK (≥70) may be acceptable for internal search. Teams should calibrate against their document corpus.

---

## ADR-11 — `--json` machine-readable CLI mode

**Context:** The default CLI output uses Rich panels and color formatting, which is not parseable by shell scripts or CI systems. Teams building automated pipelines need structured output.

**Decision:** `aksharamd compile <file> --json` suppresses all Rich formatting and prints a single valid JSON object to stdout containing: `success`, `source`, `output_dir`, `readiness_score`, `quality_band`, `warning_codes`, `errors`, `chunks`, `pages`, `optimized_tokens`, `elapsed_seconds`. If compilation fails before a manifest is produced, the same structure is printed with `success: false` and null numeric fields.

**Alternatives considered:**
- Separate `inspect` command that reads a completed manifest: requires a two-step invocation and does not cover failure cases where no manifest was written.
- JSONL streaming (one object per stage): useful for progress monitoring but complicates parsing for callers that just want the final result.

**Consequences:** `--json` implies `--quiet` (no progress output). It is compatible with `--min-readiness-score` — `success` is `false` when the threshold is not met even if compilation itself succeeded.

---

## ADR-12 — PDF parser: heading detection thresholds

**Context:** `_heading_level` compares a span's font size against the document-wide median. In 2-column academic journals, reference text (~55% of spans, 6.5–8pt) pulls the median below body text (~10pt), so body text at ratio 1.43 exceeded the old `if ratio >= 1.3: return H3` threshold. Every line of body text became a heading.

**Decision:** For ratio 1.3–1.6, require explicit heading evidence: `bold=True` OR `is_caps=True`. A word-count fallback (`≤5 words`) only fires at ratio ≥1.5. The `_prose` signal (starts lowercase/punctuation, ends `,`/`;`, contains URL) is applied at all heading levels, not just H3.

`is_caps` excludes texts containing both comma and period — `str.isupper()` returns `True` for `"CA, USA."` because punctuation is ignored, causing geographic abbreviations to trigger the all-caps heading path.

**Do not revert to the unconditional `return H3` at ratio 1.3.** It catastrophically breaks 2-column academic journals.

---

## ADR-13 — PDF parser: table detection geometry

**Context:** PyMuPDF's `page.find_tables()` fires on any page with drawing lines, including decorative page borders. A PDF with a single rectangular border on every page produced 5 spurious table extractions per page.

**Decision:** `_has_ruled_table` uses interior intersection geometry. A decorative border only has lines that meet at corners. A real table grid has column-divider lines that cross row-divider lines at interior points. A secondary fallback handles horizontal-only ruled tables (≥3 h-lines within 15% of median width). pdfplumber is used for borderless tables with `vertical_strategy="text"` and `min_words_vertical=5` (raised from 3 to prevent title-page and sidebar blocks from being detected as tables).

Rectangle drawing items are captured as h-lines/v-lines when `height > 1pt` (not `> 5pt`). The original 5pt threshold silently dropped thin horizontal rule rectangles (1–4pt, common in professionally typeset PDFs), causing the h-line similarity fallback to miss genuine ruled tables. Lowering to 1pt does not affect the interior intersection path — a page border's v-lines remain at its own x-endpoints and still produce zero interior crossings. A single decorative rule rectangle still contributes only 2 h-lines (below the ≥3 threshold for the similarity fallback).

pdfplumber bboxes are flipped to PyMuPDF coordinates before storing. Do not remove this flip.

**Do not replace interior intersection geometry with raw line counts.** The old heuristic incorrectly triggered on pages with horizontal rules that were not table row-dividers.

---

## ADR-14 — PDF parser: span deduplication thresholds

**Context:** Running headers and footers appear on every page and should be removed. The original threshold `max(2, int(page_count × 0.4))` set the floor at 2, which on a 2-page email receipt caused every repeated dollar amount and line-item label to be stripped — financial data disappeared entirely.

**Decision:** Floor raised to `max(3, ...)`. The global_threshold path additionally requires zone membership (the span must also appear in the header or footer zone) before removing it. This prevents repetitive body text on 4-page PDFs from being stripped.

**Do not lower the floor back to 2.** Short PDFs routinely have the same text block on every page by design, not because it is a running header. Do not revert the zone-membership guard — the zone path already catches all legitimate boilerplate.

---

## ADR-15 — PDF parser: paragraph gap detection

**Context:** Multi-column academic papers accumulated all text within a column into one massive paragraph block — there was no mechanism to detect vertical space between paragraphs.

**Decision:** After sorting spans into column-then-y order, flush a paragraph when the next span in the same column has a baseline-to-baseline y-gap larger than 1.8× the previous span's font size. This sits safely above same-paragraph line spacing (~1.2×) and below typical paragraph gaps (1.8–2.5×).

Tighter values (1.5×) risk splitting long lines that have sub/superscripts. Rolling-average line spacing was considered and rejected as overly complex for the improvement it would provide.

---

## ADR-16 — PDF parser: booktabs (horizontal-rule-only) table detection

**Context:** Many professionally typeset books (Wiley Dummies series, academic textbooks) use booktabs-style tables with only horizontal rules separating rows — no vertical column lines. PyMuPDF's `find_tables()` requires a full grid to detect a table. pdfplumber's text strategy fails because multi-level cell content (multiple y-positions within one logical row) confuses its column detector.

**Decision:** Add `_try_hrule_table()` as a third fallback called only when both PyMuPDF and pdfplumber find nothing. It:
1. Collects h-rules from `get_drawings()` (dx > 20% page width, dy < 3).
2. Groups rules by x-extent into candidate tables (tolerance 20pt).
3. Uses x-position gaps ≥ 20pt in the span distribution to detect column boundaries, using only spans below the first rule to avoid merged header cells inflating the column count.
4. Assigns spans to (row, column) buckets; sorts within each cell by `(round(y/5), x)` so adjacent glyphs on the same baseline (e.g. "2", "×", "3 = 6") remain in left-to-right order even when their y-coordinates differ by sub-pixel amounts.
5. Detects caption rows (first non-empty cell matches `_TBL_CAPTION_RE`) and excludes them from the table markdown so the column-header row becomes the table's first row. The table bbox is also adjusted to start at the rule after the caption, so those spans are not suppressed from the prose stream.

Caption exclusion is critical for cross-page stitching: when the same column-header row appears at the top of the continuation page, `_stitch_page_break_tables` Case 1 (identical headers) merges the two table fragments automatically.

**Do not** remove the caption exclusion or shrink the `min_col_gap` below 20pt — smaller values incorrectly split formula glyphs ("2", "×", "6 = 12") into separate columns.

---

## ADR-17 — PDF parser: deferred table block insertion

**Context:** `_process_raw_page` always prepended TABLE blocks before all prose blocks. On pages where the table appears at the bottom (after paragraphs, headings, captions), the table rendered before the prose that precedes it in the document.

**Decision:** TABLE blocks are no longer inserted immediately. They are collected in `pending_tables: list[(y_top, Block)]`. During span processing, before handling each prose span, any pending table whose `y_top ≤ span["y"]` is flushed (with a prose paragraph boundary) and inserted into the block list. After all spans are processed, any remaining pending tables (those whose y_top exceeds all prose on the page) are appended last.

This preserves the existing behaviour for pages where the table leads the content (y_top < first prose span y), while fixing pages where prose precedes the table.

**Do not revert to "tables come first."** The original approach was wrong for any page where the table is not the first content element.
