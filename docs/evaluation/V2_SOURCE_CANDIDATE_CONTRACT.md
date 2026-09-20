# Exploratory V2 source-candidate assessment

`aksharamd.assessment.assess_source_candidate` compares the exact bytes of an
original source with the exact Markdown bytes emitted by any parser. It is an
exploratory contract and does not alter, overwrite, or reinterpret the frozen
V1 readiness score or its results.

The receipt deliberately has no combined scalar. It keeps two evidence groups:

- `candidate_intrinsic`: existing Markdown fence-balance and text-integrity
  checks, evaluated without making claims about the source.
- `source_comparison`: source-identity binding, PDF text-token retention, and
  PDF-table-count retention.

Each detector reports its ID and version, scope, eligibility, whether it was
activated or abstained, a verdict, a detector-local score when activated, raw
measurements, and finding codes. An abstention always contains a reason and is
never assigned a score. Receipt models are frozen, strict, and reject unknown
fields or unsupported schema/policy/implementation identifiers. The assessment
boundary revalidates both artifacts' current byte length and SHA-256, even if a
caller mutates a historically mutable `Artifact` after construction.

## Policy v2 exploratory thresholds

PDF text retention compares a case-folded Unicode token multiset from the PDF
text layer with the candidate. It activates only for sources containing at
least 20 tokens. Retention of at least 95% passes, 80–95% is a concern, and
less than 80% fails. This proves bounded token retention, not reading order or
semantic equivalence.

PDF table retention uses PyMuPDF source geometry and exact one-to-one matching
of normalized table cell-sequence signatures against Markdown tables parsed by
markdown-it. Equal counts with unrelated content fail. A duplicated candidate
table is a concern rather than a pass. Signature matching still does not prove
reading order, semantics, or visual fidelity.

## Work limits and abstention

Inspection is local and bounded. The policy abstains rather than returning a
partial score when any applicable limit is exceeded:

- source PDF bytes: 50 MiB;
- PDF pages: 500;
- extracted PDF text: 2,000,000 characters and 250,000 tokens;
- table-geometry pages: 100;
- PDF drawing paths: 20,000;
- source or candidate tables: 200;
- source or candidate table cells: 10,000;
- candidate bytes: 20 MiB and candidate tokens: 250,000.

Text extraction and table geometry have separate error/limit states. A table
failure on one page causes the table detector to abstain but does not discard
valid text-retention evidence, and vice versa. Missing-token evidence reports a
count and at most 20 sample tokens; it never materializes an unbounded missing
token list.

## API

```python
from aksharamd.assessment import (
    CandidateArtifact,
    SourceArtifact,
    assess_source_candidate,
)

result = assess_source_candidate(source=source, candidate=candidate)
payload = result.model_dump(mode="json")
```

Both artifacts validate their SHA-256 identity at construction. Parser name,
version, configuration ID, declared source hash, artifact hashes, policy ID,
schema version, and implementation version are carried in the result. This
makes the API suitable for deterministic exploratory rescoring while keeping
the original V1 files immutable.
