# RAG Integration Guide

AksharaMD can compile documents and attach extraction diagnostics before indexing. Its **AI Readiness Score** summarizes format baselines and modeled penalties. It does not tell you whether the extraction is reliable enough to embed, whether meaning survived, or whether answers will be correct.

Use diagnostics to prioritize investigation. Acceptance requires separately validated source/task criteria, including for HIGH outputs. The examples below use **application-supplied review hooks**; these are integration placeholders, not AksharaMD APIs or a validated acceptance policy. They should inspect the source, candidate, and task requirements independently of the score and decline when evidence is insufficient.

The compiler score, default schema-1.1 saved-artifact assessment, and exploratory schema-2.0 PDF observations have different scopes. See [interface boundaries](../README.md#how-it-works) and the [evaluation policy](evaluation-claims.md). A saved-assessment gate pass only establishes the configured comparisons.

---

## Diagnostic-assisted ingestion

```python
from aksharamd.compiler import Compiler

compiler = Compiler(output_dir="output")

def ingest_document(path: str) -> None:
    text, ctx = compiler.compile_to_string(path)
    m = ctx.manifest
    print(f"DIAGNOSTICS {path}: score {m.readiness_score}/100 ({m.quality_band})")
    for note in m.confidence_notes:
        print(f"  {note}")

    # Application-supplied source/task validation applies to every band.
    if not application_review_accepts(path, text, ctx):
        route_to_review_queue(path, ctx)
        return

    embed_chunks(ctx)
```

---

## Embedding chunks

AksharaMD produces pre-sized semantic chunks in `ctx.chunks`. Each chunk carries heading, page-range, and block-ID metadata. Retain it for source inspection; metadata alone does not prove a citation or source association is correct. The following embedding example assumes the source/task review above has passed.

**Configuring chunk size and overlap.** The default chunk size is 512 tokens with no overlap. Adjust these to match your embedding model's context window and your retrieval strategy. Both values are recorded in `manifest.json` so your output is reproducible.

```bash
# CLI
aksharamd compile report.pdf --chunk-size 768 --chunk-overlap 100
```

```python
# Python API
compiler = Compiler(output_dir="output", chunk_size=768, chunk_overlap=100)
```

`chunk_overlap` must be less than `chunk_size`. Overlap is block-granular: tail blocks from the end of one chunk are carried into the start of the next when a token-limit break occurs. Heading-based section breaks always start clean with no carry-over.

```python
from aksharamd.compiler import Compiler

compiler = Compiler(output_dir="output")
text, ctx = compiler.compile_to_string("report.pdf")

for chunk in ctx.chunks:
    vector_store.add(
        text=chunk.content,
        metadata={
            "source":       ctx.manifest.source,
            "chunk_index":  chunk.index,
            "heading":      chunk.heading,
            "page_start":   chunk.page_start,
            "page_end":     chunk.page_end,
            "token_count":  chunk.token_count,
            "score":        ctx.manifest.readiness_score,
            "quality_band": ctx.manifest.quality_band,
        }
    )
```

---

## Per-block provenance categories

The `confidence` field describes how blocks were produced. EXTRACTED, INFERRED, and AMBIGUOUS are provenance categories, not calibrated probabilities or proof that the content is correct. Filtering them is an inspection aid and can omit required content; it does not approve ingestion.

```python
from aksharamd.models.block import ExtractionConfidence

text, ctx = compiler.compile_to_string("report.pdf")

# Separate native-structure blocks for inspection, without approving ingestion.
native_blocks = [
    b for b in ctx.document.blocks
    if b.confidence == ExtractionConfidence.EXTRACTED
]

ambiguous_blocks = [
    b for b in ctx.document.blocks
    if b.confidence == ExtractionConfidence.AMBIGUOUS
]

if ambiguous_blocks:
    print(f"{len(ambiguous_blocks)} ambiguous blocks — review before indexing")
```

Provenance categories:

- `EXTRACTED` — parsed from native structure (text layer, DOM, schema)
- `INFERRED` — derived heuristically (whitespace tables, font-size headings)
- `AMBIGUOUS` — extraction paths marked ambiguous (OCR, olefile stream, binary fallback); inspect their evidence

---

## Corpus ingestion

For a directory of documents, use `compile_corpus` which handles token-budget packing and near-duplicate skipping automatically.

```python
from aksharamd.compiler import Compiler
import json

compiler = Compiler(output_dir="output/.cache")
chunks = compiler.compile_corpus(
    "./documents/",
    token_budget=8_000,       # max tokens per chunk group
    dedup_threshold=0.5,      # Jaccard similarity threshold for near-duplicate skipping
)

# chunks is a list of corpus chunk groups — each group contains multiple documents
for group in chunks:
    print(f"Group {group['chunk_index']}: {len(group['documents'])} docs, "
          f"{group['token_count']:,} tokens")
    for doc in group["documents"]:
        # Application hook must validate each source and its candidate.
        if application_review_accepts(doc["source"], doc["markdown"], doc):
            embed(doc["markdown"], metadata={"source": doc["source"]})
```

Or from the CLI:

```bash
aksharamd corpus ./documents/ --budget 8000 -o corpus.json
```

---

## LangChain-style integration

AksharaMD does not depend on LangChain, but the output format is compatible. Here is an illustrative loader that wraps `compile_to_string`, applies a caller-supplied review callback, and returns LangChain `Document` objects:

```python
from __future__ import annotations
from pathlib import Path
from typing import Iterator

from langchain_core.documents import Document as LCDocument
from langchain_core.document_loaders import BaseLoader

from aksharamd.compiler import Compiler


class AksharaMDLoader(BaseLoader):
    """LangChain document loader backed by AksharaMD with application review."""

    def __init__(
        self,
        file_path: str,
        accept_candidate,        # callable(path, text, context) -> bool
        output_dir: str = "output",
        min_score: int = 70,      # optional diagnostic floor; passing is insufficient
    ) -> None:
        self.file_path = file_path
        self.accept_candidate = accept_candidate
        self.compiler = Compiler(output_dir=output_dir)
        self.min_score = min_score

    def lazy_load(self) -> Iterator[LCDocument]:
        text, ctx = self.compiler.compile_to_string(self.file_path)
        m = ctx.manifest

        if m.readiness_score < self.min_score:
            raise ValueError(
                f"Readiness score {m.readiness_score}/100 is below threshold {self.min_score}. "
                f"Quality band: {m.quality_band}. "
                f"Warnings: {m.warning_codes}"
            )

        if not self.accept_candidate(self.file_path, text, ctx):
            raise ValueError("Source/task review required before indexing")

        for chunk in ctx.chunks:
            yield LCDocument(
                page_content=chunk.content,
                metadata={
                    "source":         m.source,
                    "file_type":      m.file_type,
                    "chunk_index":    chunk.index,
                    "heading":        chunk.heading,
                    "page_start":     chunk.page_start,
                    "page_end":       chunk.page_end,
                    "token_count":    chunk.token_count,
                    "readiness_score": m.readiness_score,
                    "quality_band":   m.quality_band,
                },
            )


# Usage:
# loader = AksharaMDLoader("report.pdf", application_review_accepts, min_score=70)
# docs = loader.load()
# vectorstore = Chroma.from_documents(docs, embedding=OpenAIEmbeddings())
```

---

## LlamaIndex-style integration

Similarly, a `BaseReader` wrapper that yields LlamaIndex `Document` objects:

```python
from __future__ import annotations
from pathlib import Path
from typing import Any

from llama_index.core import Document as LIDocument
from llama_index.core.readers.base import BaseReader

from aksharamd.compiler import Compiler


class AksharaMDReader(BaseReader):
    """LlamaIndex document reader backed by AksharaMD with application review."""

    def __init__(self, accept_candidate, output_dir: str = "output", min_score: int = 70) -> None:
        self.accept_candidate = accept_candidate  # callable(path, text, context) -> bool
        self.compiler = Compiler(output_dir=output_dir)
        self.min_score = min_score

    def load_data(self, file: Path, extra_info: dict[str, Any] | None = None) -> list[LIDocument]:
        text, ctx = self.compiler.compile_to_string(str(file))
        m = ctx.manifest

        if m.readiness_score < self.min_score:
            raise ValueError(
                f"Readiness score {m.readiness_score}/100 is below threshold {self.min_score}. "
                f"Quality band: {m.quality_band}."
            )

        if not self.accept_candidate(str(file), text, ctx):
            raise ValueError("Source/task review required before indexing")

        docs = []
        for chunk in ctx.chunks:
            docs.append(LIDocument(
                text=chunk.content,
                metadata={
                    "source":          m.source,
                    "file_type":       m.file_type,
                    "chunk_index":     chunk.index,
                    "heading":         chunk.heading or "",
                    "page_start":      chunk.page_start,
                    "page_end":        chunk.page_end,
                    "readiness_score": m.readiness_score,
                    "quality_band":    m.quality_band,
                    **(extra_info or {}),
                },
            ))
        return docs


# Usage:
# reader = AksharaMDReader(application_review_accepts, min_score=70)
# documents = reader.load_data(Path("report.pdf"))
# index = VectorStoreIndex.from_documents(documents)
```

---

## Handling RISKY documents

When a document scores RISKY, the right response depends on your use case:

**Option 1: Rerun with extras**

```python
import importlib.util

text, ctx = compiler.compile_to_string("scanned.pdf")
m = ctx.manifest

if m.quality_band == "RISKY" and "OCR_REQUIRED" in m.warning_codes:
    if importlib.util.find_spec("pytesseract"):
        # OCR is installed — something else is wrong
        route_to_review_queue("scanned.pdf", m)
    else:
        print("Install aksharamd[ocr], rerun, and inspect the result.")
```

**Option 2: Record diagnostics in the review queue**

```python
if m.quality_band == "RISKY":
    route_to_review_queue("scanned.pdf", ctx)
```

**Option 3: Inspect blocks by provenance**

```python
if m.quality_band == "RISKY":
    native_content = "\n\n".join(
        b.content for b in ctx.document.blocks
        if b.confidence.value == "extracted"
    )
    # Inspect this subset against the source. EXTRACTED does not mean safe,
    # and dropping other blocks can remove facts required by your task.
    route_to_review_queue("scanned.pdf", ctx)
```

---

## Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `AKSHARAMD_MAX_FILE_BYTES` | `524288000` (500 MB) | Reject files larger than this before parsing |
| `AKSHARAMD_MAX_ARCHIVE_BYTES` | `536870912` (512 MB) | Reject archives whose declared uncompressed size exceeds this |
| `AKSHARAMD_OCR_DPI` | `200` | DPI for OCR rendering of image pages |
| `AKSHARAMD_WHISPER_MODEL` | `base` | Whisper model size (validated against allowlist: `tiny`, `base`, `small`, `medium`, `large`) |
