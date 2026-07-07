# RAG Integration Guide

AksharaMD is designed to sit directly in front of a vector store. Its primary value in a RAG pipeline is not just token reduction — it is the **AI Readiness Score**, which tells you whether a document's extraction is reliable enough to embed before you embed it.

Without a quality gate, bad extractions silently pollute your vector store. A scanned PDF, a table-heavy report with garbled OCR, or a document with CID font artifacts can produce output that looks complete and embeds without error — until your LLM gives a wrong answer, and by then the bad data is already indexed.

---

## Basic readiness-gated ingestion

The minimal integration: compile, check the score, then decide.

```python
from aksharamd.compiler import Compiler

compiler = Compiler(output_dir="output")

def ingest_document(path: str) -> None:
    text, ctx = compiler.compile_to_string(path)
    m = ctx.manifest

    if m.quality_band == "POOR":
        # Block — do not embed
        print(f"BLOCKED {path}: score {m.readiness_score}/100 ({m.quality_band})")
        for note in m.confidence_notes:
            print(f"  {note}")
        return

    if m.quality_band == "RISKY":
        # Flag for review rather than silently embedding
        print(f"FLAGGED {path}: score {m.readiness_score}/100 — routing for review")
        route_to_review_queue(path, ctx)
        return

    # HIGH or OK — embed
    embed_chunks(ctx)
    print(f"INGESTED {path}: score {m.readiness_score}/100 ({m.quality_band}), "
          f"{m.chunks} chunks, {m.optimized_tokens:,} tokens")
```

---

## Embedding chunks

AksharaMD produces pre-sized semantic chunks in `ctx.chunks`. Each chunk carries its heading, page range, and block IDs — pass these as metadata to your vector store so you can cite the source at retrieval time.

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

## Per-block confidence

For stricter pipelines, you can filter blocks by extraction confidence before chunking or embedding.

```python
from aksharamd.models.block import ExtractionConfidence

text, ctx = compiler.compile_to_string("report.pdf")

# Only embed content extracted with high confidence
clean_blocks = [
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

Confidence values:
- `EXTRACTED` — cleanly parsed from native structure (text layer, DOM, schema)
- `INFERRED` — derived with moderate uncertainty (whitespace tables, font-size headings)
- `AMBIGUOUS` — low-fidelity (OCR, olefile stream, binary fallback) — verify before relying on

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
        embed(doc["markdown"], metadata={"source": doc["source"]})
```

Or from the CLI:

```bash
aksharamd corpus ./documents/ --budget 8000 -o corpus.json
```

---

## LangChain-style integration

AksharaMD does not depend on LangChain, but the output format is compatible. Here is a minimal loader that wraps `compile_to_string` and returns LangChain `Document` objects:

```python
from __future__ import annotations
from pathlib import Path
from typing import Iterator

from langchain_core.documents import Document as LCDocument
from langchain_core.document_loaders import BaseLoader

from aksharamd.compiler import Compiler


class AksharaMDLoader(BaseLoader):
    """LangChain document loader backed by AksharaMD with readiness gating."""

    def __init__(
        self,
        file_path: str,
        output_dir: str = "output",
        min_score: int = 70,      # block documents scoring below this
    ) -> None:
        self.file_path = file_path
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
# loader = AksharaMDLoader("report.pdf", min_score=70)
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
    """LlamaIndex document reader backed by AksharaMD with readiness gating."""

    def __init__(self, output_dir: str = "output", min_score: int = 70) -> None:
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
# reader = AksharaMDReader(min_score=70)
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
        print("Install aksharamd[ocr] and rerun for full extraction.")
```

**Option 2: Ingest with a risk flag**

```python
if m.quality_band == "RISKY":
    embed_chunks(ctx, extra_metadata={"needs_review": True, "warning_codes": m.warning_codes})
```

**Option 3: Use per-block confidence to filter**

```python
if m.quality_band == "RISKY":
    # Only embed EXTRACTED blocks; skip AMBIGUOUS blocks
    safe_content = "\n\n".join(
        b.content for b in ctx.document.blocks
        if b.confidence.value == "extracted"
    )
    embed(safe_content)
```

---

## Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `AKSHARAMD_MAX_FILE_BYTES` | `524288000` (500 MB) | Reject files larger than this before parsing |
| `AKSHARAMD_MAX_ARCHIVE_BYTES` | `536870912` (512 MB) | Reject archives whose declared uncompressed size exceeds this |
| `AKSHARAMD_OCR_DPI` | `200` | DPI for OCR rendering of image pages |
| `AKSHARAMD_WHISPER_MODEL` | `base` | Whisper model size (validated against allowlist: `tiny`, `base`, `small`, `medium`, `large`) |
