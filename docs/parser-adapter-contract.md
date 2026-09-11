> **Status: EXPERIMENTAL.** These types define the intended integration boundary but are not yet wired into `Compiler`. Callers cannot yet substitute a `ParserAdapter` and have the Compiler validate the returned digest — that wiring is a tracked follow-up. Treat this document as a design contract, not a shipped guarantee.

# Parser adapter contract

`aksharamd.parser_contract` is the narrow boundary for applications that
choose their own parser. It does not change the existing plugin pipeline.

An adapter implements `ParserAdapter.parse(source)`:

1. `ParserInput` supplies immutable source bytes, a caller-defined stable
   `source_id`, the SHA-256 `source_hash`, and the source MIME type.
2. The adapter returns `ParsedArtifact`, carrying the source identity,
   parser name/version/configuration identity, output MIME type, and `content`.
3. `content` is the final text delivered to tokenizers, indexes, and LLMs.
4. `truncated` and `preview` are explicit. A preview must also be marked
   truncated, so quality gates cannot mistake a partial result for complete.

The contract validates source hashes and required identities at construction.
Parser-specific diagnostics belong in `metadata` until a cross-parser metric
is defined.
