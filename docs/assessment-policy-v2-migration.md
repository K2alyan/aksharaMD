# Assessment default migration: general-ingestion-v2

The live `Assessor.assess()` and `aksharamd assess` defaults are now
`general-ingestion-v2`. Saved compiler assessments and the optional index
assessment gate use that same default. The index gate remains opt-in; this
change does not make ungated indexing enforce assessments.

## What ACCEPT establishes

V2 adds the existing `source-text-preservation-v1` comparison to the v1 checks.
It requires UTF-8 `text/plain` or `text/markdown` source and candidate evidence.
It compares case-sensitive words and punctuation, permits simple prose line
wrapping and narrowly defined Markdown decoration, retains structured line
bindings, and requires exact lines for identifiable indentation/code. Unique
explicit label:value lines may reorder as whole lines.

This is a bounded textual invariant, not semantic fidelity, parser accuracy,
calibrated confidence, task usefulness, or source truth. Evidence explicitly
records `semantic_fidelity_established: false`. In particular, an unchanged
source placeholder can satisfy preservation; no placeholder vocabulary or
substance detector has been introduced.

Differences outside those allowances ABSTAIN unless a concrete failure already
requires HOLD. Unsupported binary/HTML/JSON evidence and missing source evidence
ABSTAIN. Critical missing literals, identity mismatch, and other existing
concrete failures still HOLD. In v2 a candidate declaring truncation HOLDs even
when its source is missing or binary. Compiler and index adapters recognize
both document metadata keys `truncated` and `declared_truncated`; either true
value declares a partial candidate.

## Compatibility and replay

Use `aksharamd assess candidate.md --source source.txt --policy general-ingestion-v1`
or `Assessor().assess(..., policy_id="general-ingestion-v1")` to replay historical
v1 behavior. V1 remains the literal-coverage policy, including its known unsafe
acceptances. `GENERAL_INGESTION_POLICY_ID` deliberately still identifies v1 for
existing frozen benchmark callers. New live callers should use
`DEFAULT_ASSESSMENT_POLICY_ID`. The serialized result's policy ID identifies
which rules ran. Historical result-model defaults remain v1 to avoid relabeling
old records without a policy field; the assessor always writes an explicit ID.

The separately selectable `source-text-preservation-v1` and the frozen confidence
experiment are unchanged. No confidence thresholds, feature extraction, corpus
splits, calibration, or old evidence artifacts were changed.

Compiler/index integrations produce v2 assessments; callers needing historical
replay can assess the saved source/output pair explicitly with v1. Already saved
v1 ACCEPT records are not automatically rescored or invalidated by this patch.
Rescore artifacts with v2 before treating historical acceptances as v2 evidence.

## Expected operational impact and verification

Some valid conversions will now ABSTAIN, including layout changes beyond the
bounded allowances and binary-source conversions. Review or obtain appropriate
source-grounded evidence; an ABSTAIN does not by itself prove corruption.
The public release must not advertise this policy as universal document fidelity.

`tests/test_assessment_default_policy.py` exercises default CLI and index safety
for placeholder replacement, negation deletion, swapped values, and dropped tail;
explicit v1 replay; clean controls; unsupported sources; both truncation aliases;
and saved compiler policy binding. The existing literal/task tests retain their
specific successful evidence assertions while no longer equating literal presence
with full acceptance.

## Delivery binding and validation

Before gated CLI promotion, the gate rereads staged `document.md` and the source,
checks their hashes against the assessment, verifies the original captured source
identity, and checks the report's provenance hashes and sizes. A later exporter
that changes either artifact blocks activation. Successful CLI JSON names the
active output directory. The report's candidate `storage_reference` is relative
to the report directory (`document.md`), so it survives promotion.

The opt-in index gate renders each selected block as Markdown, assesses the exact
joined payload, then embeds and stores those same per-block strings. Ungated
indexing retains its legacy text representation. This can abstain when rendering
changes literal code layout; abstention prevents indexing unverified output.

V2's structural fence check uses CommonMark fence parsing, including delimiter
length, tilde fences, and quote/list containers. Historical v1 fence checks remain
unchanged. Task profiles and literal relationships reject unknown fields, and
profiles reject unsupported schema versions, so unsupported requirements cannot
silently disappear.
