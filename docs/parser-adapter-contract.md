> **Status: shipped.** The Compiler accepts a `ParserAdapter` via
> `Compiler(parser_adapter=...)`. Returned `ParsedArtifact` objects are
> validated (source_hash, source_id, content_mime_type, content_hash) before
> downstream processing. Legacy `parsers=` behaviour is preserved byte-identically
> when `parser_adapter` is not supplied.

# Parser adapter contract

`aksharamd.parser_contract` is the narrow boundary for applications that
choose their own parser. It does not change the existing plugin pipeline.

An adapter implements `ParserAdapter.parse(source)`:

1. `ParserInput` supplies immutable source bytes, a caller-defined stable
   `source_id`, the SHA-256 `source_hash`, and the source MIME type.
2. The adapter returns `ParsedArtifact`, carrying the source identity,
   parser name/version/configuration identity, `content` bytes, `content_hash`,
   and `content_mime_type`.
3. `content` is the final markdown or plain-text delivered to tokenizers,
   indexes, and LLMs. The Compiler will re-ingest it through the built-in
   markdown pipeline before cleaning, validation, and readiness scoring.
4. `declared_truncated` marks explicit partial results so quality gates cannot
   mistake a truncated response for complete output.

The contract validates source and content hashes at construction. Parser-specific
diagnostics belong in `metadata` until a cross-parser metric is defined.

## Failure codes

When the Compiler rejects an adapter response, `ctx.validation.errors`
carries one of the following codes and `ctx.document` is left as `None`:

- `ADAPTER_SOURCE_HASH_MISMATCH` — the artifact's `source_hash` does not
  equal the `ParserInput.source_hash` the Compiler passed in.
- `ADAPTER_SOURCE_ID_MISMATCH` — the artifact's `source_id` does not equal
  the `ParserInput.source_id`.
- `ADAPTER_UNSUPPORTED_MEDIA_TYPE` — the artifact's `content_mime_type` is
  not one of the accepted types (`text/markdown`, `text/plain` in the
  initial release).
- `ADAPTER_CONTENT_HASH_MISMATCH` — `sha256(artifact.content).hexdigest()`
  does not equal `artifact.content_hash`.
