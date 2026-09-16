"""Synthetic JATS fixtures for the PMC textual-oracle eligibility V2 predicate.

Every fixture is a full JATS XML document constructed in-test so the
suite depends on nothing beyond the merged instrument. No network,
no filesystem outside ``tmp_path``, no real PMC document. The fixtures
exercise ``apply_text_oracle_filters`` end-to-end, including the
canonical ``_transform_jats`` path it calls into.
"""
from __future__ import annotations

from benchmarks.eval_v1.acquisition.pmc_oa_aws import PmcVersionMetadata
from benchmarks.eval_v1.selection.pmc_oa_text_oracle_eligibility import (
    PMC_MIN_BODY_TOKENS,
    PMC_TEXT_ORACLE_ELIGIBILITY_VERSION,
    TextOracleDecision,
    apply_text_oracle_filters,
)

# ---------------------------------------------------------------------
# Metadata fixture — passes V1 metadata filter unless overridden
# ---------------------------------------------------------------------


def _md(**overrides: object) -> PmcVersionMetadata:
    defaults: dict[str, object] = dict(
        pmcid="PMC0000001",
        version=1,
        title="Test article",
        citation=None,
        doi=None,
        pmid=None,
        license_code="CC BY",
        is_pmc_openaccess=True,
        is_retracted=False,
        is_manuscript=False,
        is_historical_ocr=False,
        mid=None,
        pdf_url=(
            "https://pmc-oa-opendata.s3.amazonaws.com/PMC0000001.1/"
            "PMC0000001.1.pdf?md5=deadbeef"
        ),
        xml_url=(
            "https://pmc-oa-opendata.s3.amazonaws.com/PMC0000001.1/"
            "PMC0000001.1.xml?md5=deadbeef"
        ),
        text_url=None,
        media_urls=(),
        raw={"pmcid": "PMC0000001", "version": 1},
    )
    defaults.update(overrides)
    return PmcVersionMetadata(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------
# JATS fixture builders
# ---------------------------------------------------------------------


def _n_word_paragraph(n_words: int, seed: str = "word") -> str:
    """Return a paragraph text with exactly ``n_words`` tokenizable tokens.

    Each word is a distinct alphanumeric string so ``_TOKEN_RE.findall``
    counts them deterministically.
    """
    return " ".join(f"{seed}{i:04d}" for i in range(n_words))


def _jats(
    *,
    article_type: str = "research-article",
    has_body: bool = True,
    body_content: str = "",
    include_abstract: bool = True,
    abstract_content: str = "Abstract text.",
    include_table: bool = False,
    include_fig: bool = False,
    ref_list_content: str | None = None,
) -> bytes:
    """Build a minimal but well-formed JATS XML document.

    - ``body_content`` becomes a single ``<p>`` inside ``<body>`` when
      ``has_body`` is True. Pass an empty string to build a body with
      no substantive paragraphs.
    - ``ref_list_content`` is the text of a synthetic reference-list
      entry appended to ``<back>``. The oracle transformation excludes
      ``<back>`` from body extraction, so tokens under here contribute
      to file token count but not body token count. Used by the
      "outside-body tokens" regression test.
    """
    parts: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append(f'<article article-type="{article_type}">')
    parts.append("<front><article-meta>")
    parts.append('<article-id pub-id-type="pmc">PMC0000001</article-id>')
    parts.append("<title-group><article-title>Test title</article-title></title-group>")
    if include_abstract:
        parts.append(f"<abstract><p>{abstract_content}</p></abstract>")
    parts.append("</article-meta></front>")

    if has_body:
        parts.append("<body>")
        if body_content:
            parts.append(f"<sec><title>Section</title><p>{body_content}</p></sec>")
        if include_table:
            parts.append(
                "<sec><title>Results</title>"
                "<table-wrap id='T1'><caption><p>Table caption</p></caption>"
                "<table><tr><th>A</th><th>B</th></tr>"
                "<tr><td>1</td><td>2</td></tr></table></table-wrap></sec>"
            )
        if include_fig:
            parts.append(
                "<sec><title>Figures</title>"
                "<fig id='F1'><caption><p>Figure caption</p></caption></fig></sec>"
            )
        parts.append("</body>")

    if ref_list_content:
        parts.append(
            f"<back><ref-list><ref id='R1'>{ref_list_content}</ref></ref-list></back>"
        )

    parts.append("</article>")
    return "\n".join(parts).encode("utf-8")


# ---------------------------------------------------------------------
# Version + constant guardrails
# ---------------------------------------------------------------------


def test_version_constants_are_pinned() -> None:
    assert PMC_TEXT_ORACLE_ELIGIBILITY_VERSION == "2"
    assert PMC_MIN_BODY_TOKENS == 500


# ---------------------------------------------------------------------
# Metadata-gate check (V1 predicate is invoked first)
# ---------------------------------------------------------------------


def test_metadata_ineligible_rejects_before_jats_is_touched() -> None:
    # A retracted article must never be text-oracle-eligible regardless
    # of body content — the V1 metadata rule short-circuits.
    md = _md(is_retracted=True)
    xml = _jats(body_content=_n_word_paragraph(1000))
    decision = apply_text_oracle_filters(md, xml)
    assert decision == TextOracleDecision(False, "is_retracted_true")


# ---------------------------------------------------------------------
# Nine synthetic tests locked with the reviewer
# ---------------------------------------------------------------------


def test_1_abstract_only_article_rejects() -> None:
    # article-type="abstract" AND no <body> element at all. This is
    # exactly the failure class from B1a-6 V1 (PMC6839998.1).
    md = _md()
    xml = _jats(article_type="abstract", has_body=False)
    decision = apply_text_oracle_filters(md, xml)
    assert decision.ok is False
    assert decision.reason == "jats_body_absent"


def test_2_body_absent_rejects() -> None:
    # article-type is a legitimate full-text type but the JATS carries
    # no <body> element. Should still reject; the V2 predicate keys on
    # body content, not on the type label.
    md = _md()
    xml = _jats(article_type="research-article", has_body=False)
    decision = apply_text_oracle_filters(md, xml)
    assert decision.ok is False
    assert decision.reason == "jats_body_absent"


def test_3_empty_body_rejects() -> None:
    # <body></body> present but empty. Passes the body-presence gate
    # but the canonical transformation produces an empty body_text.
    md = _md()
    xml = _jats(has_body=True, body_content="")
    decision = apply_text_oracle_filters(md, xml)
    assert decision.ok is False
    assert decision.reason == "canonical_body_text_empty"


def test_4_short_body_below_min_rejects() -> None:
    # A body carrying ~200 substantive body tokens — well under the
    # 500 floor. Exact count includes the section title emitted by the
    # canonical walker, so pin the reason prefix and just check the
    # count is < PMC_MIN_BODY_TOKENS rather than equal to a magic
    # number.
    md = _md()
    xml = _jats(body_content=_n_word_paragraph(200))
    decision = apply_text_oracle_filters(md, xml)
    assert decision.ok is False
    assert decision.reason is not None
    assert decision.reason.startswith("canonical_body_tokens_lt_500:"), decision.reason
    count = int(decision.reason.rsplit(":", 1)[1])
    assert count < PMC_MIN_BODY_TOKENS
    assert count >= 200  # at minimum, the paragraph's 200 tokens


def test_5_ordinary_research_article_at_min_accepts() -> None:
    # Exactly 500 body tokens is the floor. The predicate uses >=, so
    # accept.
    md = _md()
    xml = _jats(article_type="research-article", body_content=_n_word_paragraph(500))
    decision = apply_text_oracle_filters(md, xml)
    assert decision.ok is True
    assert decision.reason is None


def test_6_non_research_article_500plus_accepts() -> None:
    # A review-article with a proper body is not a research-article
    # bibliographically, but it satisfies the V2 predicate — we
    # deliberately do NOT gate on article-type.
    md = _md()
    xml = _jats(article_type="review-article", body_content=_n_word_paragraph(600))
    decision = apply_text_oracle_filters(md, xml)
    assert decision.ok is True


def test_7_article_without_table_accepts() -> None:
    # No <table-wrap>. This was a prove-one engineering requirement
    # (B1a-2), not an oracle-eligibility one.
    md = _md()
    xml = _jats(body_content=_n_word_paragraph(600), include_table=False,
                include_fig=True)
    decision = apply_text_oracle_filters(md, xml)
    assert decision.ok is True


def test_8_article_without_figure_accepts() -> None:
    # No <fig>. Also a prove-one engineering requirement.
    md = _md()
    xml = _jats(body_content=_n_word_paragraph(600), include_table=True,
                include_fig=False)
    decision = apply_text_oracle_filters(md, xml)
    assert decision.ok is True


def test_9_tokens_outside_body_do_not_count() -> None:
    """The regression test locked by the reviewer.

    Raw XML has *more than* PMC_MIN_BODY_TOKENS tokens IF you count
    the whole document (abstract + refs + metadata), but the body
    itself has FEWER than PMC_MIN_BODY_TOKENS tokens. Must reject.

    This guards against the specific accidental design where token
    count was taken from the whole ``core["tokens"]`` field (which
    mixes title + abstract + body_text). Body-scoped counting is the
    invariant the V2 predicate promises.
    """
    md = _md()
    abstract_400 = _n_word_paragraph(400, seed="abs")
    refs_400 = _n_word_paragraph(400, seed="ref")
    body_100 = _n_word_paragraph(100, seed="body")
    xml = _jats(
        body_content=body_100,
        abstract_content=abstract_400,
        ref_list_content=refs_400,
    )
    decision = apply_text_oracle_filters(md, xml)
    assert decision.ok is False
    assert decision.reason is not None
    assert decision.reason.startswith("canonical_body_tokens_lt_500:"), decision.reason
    count = int(decision.reason.rsplit(":", 1)[1])
    # Body carries ~100 tokens (the fixture's <title>Section</title>
    # adds one more, which still leaves the total well under 500).
    # The 400 abstract tokens and 400 ref-list tokens are OUTSIDE
    # <body> and must not be included in the canonical body count.
    assert count < PMC_MIN_BODY_TOKENS
    assert count < 200, (
        f"body token count = {count}; suggests tokens outside <body> "
        "(abstract/refs) are leaking into the eligibility count — "
        "V2 predicate must be body-scoped"
    )


# ---------------------------------------------------------------------
# Cross-consistency with the adapter (the invariant this module claims)
# ---------------------------------------------------------------------


def test_eligible_article_yields_matching_adapter_body_text() -> None:
    """If eligibility passes, the same JATS bytes produce a body_text
    of at least PMC_MIN_BODY_TOKENS tokens through the adapter's
    canonical path.

    This is the invariant the V2 predicate is contractually meant to
    establish. It exists to catch a future refactor that lets the
    two paths silently diverge.
    """
    from benchmarks.eval_v1.adapters.pmc_oa_v1 import _TOKEN_RE, _transform_jats

    xml = _jats(body_content=_n_word_paragraph(750))
    md = _md()
    assert apply_text_oracle_filters(md, xml).ok is True

    core, _stats, _tables, _captions = _transform_jats(xml)
    body_text = core["body_text"]
    assert body_text.strip()
    assert len(_TOKEN_RE.findall(body_text)) >= PMC_MIN_BODY_TOKENS
