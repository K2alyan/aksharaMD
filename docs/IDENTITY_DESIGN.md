# AksharaMD Identity and Provenance Model (v1.1)

## Overview

Every compiled document carries three distinct identifiers that answer three different questions:

| Identifier | Question | Stable across |
|---|---|---|
| `source_id` | Where did this come from? | Re-downloads, temp paths, relative/absolute path variants |
| `capture_id` | Which exact bytes were ingested? | — changes when the file changes |
| `document_id` | What IR was extracted? | Source path, compiled_at, schema_version, metadata |

Chunks inherit all three from their parent document.

---

## Derivations

### `source_id`

`sha256(normalized_locator.encode("utf-8"))[:16]`

Normalization rules:
- **Local files**: `Path(source).resolve().as_posix()` — absolute, forward-slash, no trailing slash. Relative paths and symlinks are resolved before hashing.
- **URLs** (`http://`, `https://`): verbatim original URI (the URL before any fetch to a temp file). Query strings and fragments are included as-is.
- **S3 URIs** (`s3://`): verbatim original URI.

**Override API**: all three public compile methods accept an optional `source_id: str | None = None` keyword argument. When provided, the value is used verbatim instead of the auto-derived locator hash. This is the correct path for canonical S3 key identities, mirrored files, or test fixtures that need a deterministic ID:

```python
ctx = Compiler().compile_to_string(local_path, source_id=_compute_source_id("s3://bucket/key"))
```

The override propagates to `ctx.source_id`, `ctx.manifest.source_id`, `ctx.document.source_id`, and every `chunk.source_id`.

### `capture_id`

`sha256(raw_file_bytes).hexdigest()`  — full 64-char hex, **not truncated**.

- Computed immediately after the source is available as a local path (after URL/S3 download to temp file, before parsing).
- For URL/S3 sources: the temp file bytes, which are the bytes of the downloaded document.
- Empty string (`""`) when the file was not readable (e.g., OSError before stat, or FILE_TOO_LARGE gate triggered before stat completion).
- Does not depend on chunker configuration, schema version, or any compilation option.

### `document_id`

`sha256(canonical_form.encode("utf-8"))[:16]`

Canonical form:

```
"{file_type}:{pages}:{block_serial}"
```

where `block_serial` is a semicolon-joined list sorted by `block.index`:

```
"{b.type}:{b.page or 0}:{b.index}:{b.checksum}"
```

and `b.checksum = sha256(b.content.encode("utf-8"))[:16]`.

#### Included fields
| Field | Rationale |
|---|---|
| `file_type` | Different parsers produce structurally different IRs |
| `pages` | Physical document extent |
| `b.type` | Block semantic role |
| `b.page` | Physical position (disambiguates identical content on different pages) |
| `b.index` | Ordinal position in document |
| `b.checksum` | SHA-256 of raw block content string |

#### Explicitly excluded volatile fields
| Field | Rationale |
|---|---|
| `source` | Path or URL — changes on move/rename |
| `compiled_at` | Timestamp — volatile |
| `source_id` | Derived from source — also volatile |
| `capture_id` | Byte-level fingerprint, not IR-level |
| `schema_version` | Format metadata, not content |
| `stage_timings` | Performance data |
| `metadata` dict | Parser-emitted PDF/DOCX metadata; can vary without IR change |

#### Explicitly excluded content fields (design decisions)
| Field | Rationale |
|---|---|
| `assets` | Binary blobs (images, audio); identical text extraction with different embedded images should yield the same `document_id` |
| `block.confidence` | ExtractionConfidence is a quality annotation, not content |
| `block.metadata` | Bounding boxes, x0/y0 coordinates, table_bbox — layout-specific, not semantic IR |
| `block.level` | Heading level; level changes without content change are not an IR identity change |
| `block.language` | Code language annotation |
| `title`, `author`, `created` | Document metadata, not block IR |

#### Unicode and newline policy
Before computing any content hash (block checksum or document canonical form), text is normalized via `_normalize_for_hash(text)`:

```python
unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
```

- **NFC normalization**: NFC and NFD forms of the same text produce the **same** checksum.
- **Newline normalization**: CRLF and bare CR are collapsed to LF before hashing; guards against cross-platform differences.
- Raw `block.content` is **not mutated** — normalization is applied only for hashing.

---

## Block identity

`block.id = sha256(f"{b.type}:{b.page or 0}:{b.index}:{b.checksum}".encode())[:16]`

**Collision guarantee**: Two distinct `Block` objects within a document always have different `index` values (index = position in the blocks list). Therefore two blocks with identical content on the same page receive different IDs because their indices differ.

**Assumption**: the emitting parser must assign unique, sequential indices (0, 1, 2, …). If two blocks share an index (a parser defect), they will collide when content is also identical. No runtime uniqueness check is enforced.

`page=None` serializes as `0`. This means a block with explicit `page=0` and a block with `page=None` would collide if all other fields match — but `page=0` is not a valid page number in practice (pages are 1-indexed).

---

## Chunk identity

`chunk.id = sha256(f"{document_id}:{index}:{content_digest}".encode())[:16]`

where `content_digest = sha256(chunk.content.encode())[:16]`.

**Backward compatibility**: when `document_id = ""` (pre-Phase 2 test fixtures or manual construction without a full compile), the formula degrades to `sha256(f"{index}:{content_digest}".encode())[:16]` — identical to the Phase 1 formula.

---

## `confidence_summary` schema (per chunk)

```json
{
  "extracted": {"count": 5, "block_ids": ["abc123", "def456", "..."]},
  "inferred":  {"count": 2, "block_ids": ["ghi789", "jkl012"]},
  "ambiguous": {"count": 0, "block_ids": []}
}
```

Each level reports the count of blocks at that confidence level and their IDs, enabling callers to identify which specific blocks were OCR-derived or heuristically extracted.

---

## Schema version

All models bumped from `"1.0"` → `"1.1"`: Document, Chunk, Manifest, ValidationReport.

---

## Provenance propagation matrix

| Output surface | source_id | capture_id | document_id | Policy |
|---|---|---|---|---|
| `document.json` (JSON export) | ✓ | ✓ | ✓ | Full — Document model fields |
| `manifest.json` (JSON export) | ✓ | ✓ | ✓ | Full — Manifest model fields |
| `validation.json` (JSON export) | — | — | — | Not applicable — validation issues don't need document identity; ValidationReport has no per-document ID fields by design |
| `chunks/*.json` (JSON export) | ✓ | ✓ | ✓ | Full — Chunk model fields + duplicated in chunk.metadata for consumer convenience |
| `compile_to_string()` return value | — | — | — | Design decision: returns plain markdown; IDs accessible via `ctx.manifest` |
| `compile_to_multimodal()` return value | — | — | — | Design decision: Anthropic API content array format has no provenance envelope; IDs accessible via `ctx.manifest` |
| `compile_corpus()` doc entries | — | — | — | Gap: corpus chunks carry only `source` (relative path) and confidence counts. Adding IDs would require corpus to call per-document pipeline with identity propagation. Deferred to Phase 6. |
| `CompilationContext` (in-memory) | ✓ (`ctx.source_id`) | ✓ (`ctx.capture_id`) | ✓ (`ctx.manifest.document_id`) | Full |

---

## `Document.id` compatibility

`Document.id` has been a backward-compatibility alias for `document_id` since Phase 2.

| Scenario | Before Phase 2 | After Phase 2 |
|---|---|---|
| Production compile (via `_run_pipeline`) | `""` — `compute_id()` was never called | `= document_id` — `compute_id()` called before chunking |
| Test fixtures calling `doc.compute_id()` | `sha256(source:file_type:pages)[:16]` — source-path-based | `sha256(canonical_blocks)[:16]` — content-based |
| `doc.id` after `compute_id()` | populated (volatile) | populated (stable) |

**Breaking change**: test code that constructs a `Document`, calls `compute_id()`, and asserts a specific hash value will produce a different hash. No test in the suite checks specific hash values; only `id != ""` is checked. Confirmed safe.

**Semantic improvement**: `Document.id` is now meaningful (content-stable) rather than volatile (path-dependent).

---

## Compiler pipeline insertion points

```
_run_pipeline():
  1. stat() + read bytes  →  capture_id stored in ctx.capture_id
  2. parse → clean → optimize → validate   (blocks assembled, final state)
  3. ctx.document.compute_id()             →  document_id (content-based)
  4. chunk (SemanticChunker reads doc.document_id, sets on each Chunk)
  5. tokenize → manifest creation
  6. readiness score
  7. source_id = _compute_source_id(_original_source)   [always uses original, not temp]
  8. propagate source_id + capture_id → document, manifest, each chunk
  9. restore _original_source in document.source + manifest.source  (URL/S3 only)
```

## Determinism contract vs wall-clock provenance

Not every field written to disk participates in the identity contract. The
compiler emits two disjoint classes of information:

### Deterministic / content-derived (in the contract)

Byte-for-byte stable across identical repeat runs on identical inputs:

| Field | Where it lives |
|---|---|
| `source_id` | `Document.source_id`, `Manifest.source_id`, `Chunk.source_id` |
| `capture_id` | `Document.capture_id`, `Manifest.capture_id`, `Chunk.capture_id` (subject to `capture_id`'s defined inputs — see §Derivations) |
| `document_id` | `Document.document_id`, `Manifest.document_id`, `Chunk.document_id` |
| Block `checksum` | `Document.blocks[i].checksum` |
| Block `id` | `Document.blocks[i].id` |
| Chunk `id` | `Chunk.id` (and the on-disk file name `chunks/<id>.json`) |

Consumers **may** rely on these for cache keys, deduplication, and cross-run
comparisons.

### Intentionally non-deterministic operational provenance (out of the contract)

Written to disk for auditability and observability, but sourced from a wall
clock or from measured runtime. Callers must not use these as cache keys:

| Field | Where it lives |
|---|---|
| `compiled_at` (ISO 8601) | `Document.compiled_at`, `Manifest.compiled_at`, per-chunk `compiled_at` |
| `elapsed_seconds` | `Manifest.elapsed_seconds` |
| `stage_timings.*` (per-stage durations) | `Manifest.stage_timings` |
| `file_modified_at` (the source file's mtime) | `Manifest.file_modified_at` — reflects the source, not the run |

`file_modified_at` is a wall-clock value from the source's own metadata,
not from the run itself. It is stable if the source file is not touched
between runs, but the compiler still treats it as operational provenance:
it is not part of any content hash and does not participate in
`document_id`.

### Practical consequence

Byte-for-byte equality of a full `manifest.json` (or `document.json`, or a
`chunks/<id>.json`) is **not** promised across runs — the `compiled_at`
and timing fields will differ.  Semantic / content identity via the
fields in the first table above **is** promised. Use those fields — not
whole-file digests — when comparing outputs across runs or as cache keys
in downstream pipelines.

This split is exercised end-to-end by
`tests/test_stabilization_regressions.py` (identity fields) and by
`tests/test_e2e_installed_wheel.py::test_content_derived_fields_are_deterministic`
(chunk file names + identity across repeat runs).
