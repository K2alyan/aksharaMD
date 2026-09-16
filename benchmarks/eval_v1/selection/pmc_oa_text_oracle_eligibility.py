"""PMC-OA textual-oracle eligibility, version 2.

This module ships the corrected eligibility contract for PMC-OA under
``selection_algorithm_version = "2"``. It supersedes the V1 metadata-
only predicate that admitted `PMC6839998.1` — an abstract-only record
whose JATS has no `<body>` element and therefore cannot produce the
G1 textual oracle that :class:`PmcOaV1Adapter` is contractually
required to emit. See ``docs/evaluation/DEV_PILOT_INGESTION_COMPLETION_REPORT_V1.md``
for the failure evidence that motivated V2.

## The invariant this module establishes

    If a PMC canonical id passes ``apply_text_oracle_filters``,
    :meth:`PmcOaV1Adapter.ingest_ground_truth` will be able to
    produce a ``body_text`` with at least ``PMC_MIN_BODY_TOKENS``
    canonical body tokens from the same JATS bytes.

To make that invariant hold, the eligibility check goes through the
**same** canonical JATS → oracle transformation the adapter uses
(:func:`benchmarks.eval_v1.adapters.pmc_oa_v1._transform_jats`) and
tokenizes the body with the **same** tokenizer
(:data:`benchmarks.eval_v1.adapters.pmc_oa_v1._TOKEN_RE`), not a
separate approximate XML text counter. Selection and ingestion cannot
disagree about what "500 tokens" means because they walk the same
code path over the same bytes.

## What ``PMC_MIN_BODY_TOKENS = 500`` is and is not

500 is a **pilot eligibility floor**, not an empirically-optimized
quality threshold. Its purpose is to exclude records that are not
functionally full-text documents (abstracts published as PMC articles,
letters, corrections, meeting notices) while remaining permissive
about legitimate scientific articles at the shorter end of the length
distribution.

We do **not** claim that a 499-token article is intrinsically a poor
extraction target. If the pilot demanded 750 tokens, or 300, or ran
against a different corpus, the floor would move. It is a
methodological floor for THIS pilot, deliberately weak, chosen to
exclude the observed failure class without over-fitting the sample.

## What ``apply_text_oracle_filters`` intentionally does NOT check

- ``article-type`` (e.g. "research-article"). A bibliographic type
  classification is not a proxy for oracle usability. A review article
  or clinical report with a proper body is fine; a "research-article"
  with an empty body is not.
- Presence of at least one ``<table-wrap>``.
- Presence of at least one ``<fig>``.
- ``<sec>`` count >= 2.

Those were prove-one engineering requirements (B1a-2 wanted a rich
first document to exercise the adapter). They are not necessary
conditions for a valid textual oracle.
"""
from __future__ import annotations

from dataclasses import dataclass

# defusedxml is the hardened XML frontend used across the acquisition +
# adapter layers; JATS bytes are external input and must never touch
# stdlib ElementTree.
from defusedxml import ElementTree as ET  # type: ignore[import-untyped]

from benchmarks.eval_v1.acquisition.pmc_oa_aws import (
    PmcVersionMetadata,
    apply_metadata_filters,
)
from benchmarks.eval_v1.adapters.pmc_oa_v1 import (
    _TOKEN_RE,
    _transform_jats,
)

# Version constants — kept explicit and pinned so audit trails can
# reference exactly which oracle-eligibility contract admitted a
# canonical id.
PMC_TEXT_ORACLE_ELIGIBILITY_VERSION = "2"
PMC_MIN_BODY_TOKENS = 500


@dataclass(frozen=True)
class TextOracleDecision:
    """Result of the V2 PMC textual-oracle eligibility check.

    Mirrors :class:`benchmarks.eval_v1.acquisition.pmc_oa_aws.EligibilityDecision`
    intentionally so callers can pattern-match uniformly on
    ``.ok`` / ``.reason``. The class is distinct because a text-oracle
    decision can fail for reasons that don't exist at metadata level
    (e.g. ``canonical_body_tokens_lt_500``); mixing them into one type
    would blur the distinction in provenance records.
    """

    ok: bool
    reason: str | None


def _count_body_tokens(body_text: str) -> int:
    """Canonical body-token count using the adapter's tokenizer.

    The adapter's ``core["tokens"]`` field mixes title + abstract +
    body_text (see :func:`_transform_jats`), so counting it directly
    would count text outside ``<body>``. This function tokenizes the
    canonical body_text alone, using the same regex the adapter uses,
    to keep the eligibility count strictly body-scoped.
    """
    return len(_TOKEN_RE.findall(body_text))


def apply_text_oracle_filters(
    md: PmcVersionMetadata,
    xml_bytes: bytes,
) -> TextOracleDecision:
    """Chain V1 metadata filters + JATS oracle-content checks.

    Sequence (fail-closed at each step):

    1. Existing PMC metadata eligibility (V1 rules).
    2. JATS parses cleanly.
    3. ``<body>`` element exists.
    4. Canonical JATS → oracle transformation completes.
    5. Canonical ``body_text`` is non-empty.
    6. Canonical body-token count >= :data:`PMC_MIN_BODY_TOKENS`.

    The transformation call and the tokenization use the same
    functions the adapter's :meth:`ingest_ground_truth` uses. That is
    the point of this module: if the eligibility check passes, the
    adapter will subsequently be able to emit a body oracle of at
    least the same size.
    """
    # Step 1: V1 metadata eligibility. If a record is not open-access
    # CC BY with pdf+xml urls it does not become oracle-eligible; the
    # V2 predicate is strictly a refinement of V1, not a replacement.
    md_decision = apply_metadata_filters(md)
    if not md_decision.ok:
        return TextOracleDecision(False, md_decision.reason)

    # Step 2: parse the JATS. A parse failure is a legitimate
    # eligibility rejection, not a network/integrity error — callers
    # that need to distinguish should recover this from the reason
    # code.
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        return TextOracleDecision(False, f"jats_parse_error:{e}")

    # Step 3: <body> must exist. This is the coarse gate that
    # abstract-only articles (article-type="abstract" with no <body>)
    # do not satisfy — the exact failure class from
    # DEV_PILOT_INGESTION_COMPLETION_REPORT_V1.
    body = root.find(".//body")
    if body is None:
        return TextOracleDecision(False, "jats_body_absent")

    # Step 4-6: canonical transformation, non-empty body_text,
    # >= PMC_MIN_BODY_TOKENS body tokens.
    try:
        core, _stats, _tables, _captions = _transform_jats(xml_bytes)
    except Exception as e:  # noqa: BLE001 — transformation is a hard fail
        return TextOracleDecision(
            False, f"jats_transform_error:{type(e).__name__}:{e}"
        )

    body_text = core.get("body_text", "") or ""
    if not body_text.strip():
        return TextOracleDecision(False, "canonical_body_text_empty")

    body_token_count = _count_body_tokens(body_text)
    if body_token_count < PMC_MIN_BODY_TOKENS:
        return TextOracleDecision(
            False,
            f"canonical_body_tokens_lt_{PMC_MIN_BODY_TOKENS}:{body_token_count}",
        )

    return TextOracleDecision(True, None)


__all__ = [
    "PMC_MIN_BODY_TOKENS",
    "PMC_TEXT_ORACLE_ELIGIBILITY_VERSION",
    "TextOracleDecision",
    "apply_text_oracle_filters",
]
