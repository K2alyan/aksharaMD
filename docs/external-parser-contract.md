> **Status: shipped.** `Compiler(parser_adapter=...)` accepts external parsers
> through the hash-validated `ParserAdapter` boundary. This exercise
> demonstrates the seam without an LLM, network, or model download.

# External parser contract exercise

`tests/test_external_parser_contract.py` runs a real local MarkItDown
conversion against a tiny Markdown fixture. The emitted Markdown is encoded
as the exact bytes of a `CandidateArtifact`, bound to the `SourceArtifact`
hash, and sent through the deterministic `Assessor`. This checks the seam
where a user supplied parser enters AksharaMD's canonical artifact and
assessment contracts without using an LLM, network, or model download.

MarkItDown is an optional evaluation dependency. The test skips with an
explicit reason when it is not installed:

```text
python -m pytest tests/test_external_parser_contract.py -q -o addopts=""
```

The test is marked `slow` because it imports an optional parser, though the
fixture itself is intentionally small. A future adapter exercise can follow
the same shape for PDF or image parsers, with binary source assessments
remaining `ABSTAIN` until source-grounded evidence is available.

## Failure codes

The Compiler-side wiring validates every `ParsedArtifact` returned by a
`ParserAdapter` and records one of the following codes on
`ctx.validation.errors` when validation fails:

- `ADAPTER_SOURCE_HASH_MISMATCH`
- `ADAPTER_SOURCE_ID_MISMATCH`
- `ADAPTER_UNSUPPORTED_MEDIA_TYPE`
- `ADAPTER_CONTENT_HASH_MISMATCH`
