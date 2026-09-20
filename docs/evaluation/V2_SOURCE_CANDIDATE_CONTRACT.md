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
never assigned a score.

## Policy v2 exploratory thresholds

PDF text retention compares a case-folded Unicode token multiset from the PDF
text layer with the candidate. It activates only for sources containing at
least 20 tokens. Retention of at least 95% passes, 80–95% is a concern, and
less than 80% fails. This proves bounded token retention, not reading order or
semantic equivalence.

PDF table retention compares the number of tables found from source geometry
by PyMuPDF with the number of Markdown tables parsed by markdown-it. It
activates only when at least one source table is detected. Candidate table
count at least equal to source table count passes; a deficit fails. This is a
count-preservation signal and does not establish cell-level fidelity.

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
