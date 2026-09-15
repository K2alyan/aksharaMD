"""V1 corpus adapter for PMC Open Access (textual G1).

Role in PROTOCOL_V1 (§2.2, §2.3): PMC-OA supplies **textual ground
truth**. The JATS/XML full text is authoritative for prose content,
NOT for layout or rendering. Downstream comparison uses word-set /
n-gram overlap, never character-perfect equality.

Provenance model:

- The adapter is strictly offline. It consumes ``PmcOaAsset`` records
  populated by ``benchmarks/eval_v1/acquisition/pmc_oa.py``.
- Each ``PmcOaAsset`` points at a per-article ``manifest.json`` produced
  during acquisition. That manifest is the authoritative provenance
  bundle (URLs, hashes, license, citation, PMCID, acquisition
  timestamp). The adapter surfaces the manifest onto the returned
  ``SourceIngestion`` and ``GroundTruth`` records rather than
  reconstructing provenance ad hoc.

JATS oracle transformation (``extraction_rules_version="1"``):

- **Include** in the primary textual oracle: article title, abstract
  paragraphs, ``<body>`` section titles and paragraphs (in document
  order), table cell text (with cell-boundary structure recorded
  separately), figure and table captions, footnote text.
- **Exclude** from the primary textual oracle: ``<ref-list>``
  (bibliographic references), ``<xref>`` marker text (superscript
  reference / footnote pointers — the referent is preserved
  separately), front-matter beyond title + abstract, back-matter
  supplementary sections.
- **MathML**: preserve ``alttext``, ``<tex-math>``, and textual
  children. Equations that carry none of the above are recorded as
  ``equation_unrepresented`` counters in the provenance, never silently
  dropped.
- **Whitespace normalization**: JATS is structured source text (not
  PDF-extracted), so normalization is limited to collapsing runs of
  whitespace and stripping leading/trailing whitespace on extracted
  text nodes. **No PDF-style dehyphenation is applied** — introducing
  it would manufacture transformations JATS never had.

The ground-truth ``data`` payload preserves canonical text and ordered
tokens with multiplicity. Any set-based derivation is the metric's
responsibility, not the oracle's — the oracle must not bake in a lossy
representation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from benchmarks.eval_v1.corpus_adapter import (
    CorpusCapabilities,
    GroundTruth,
    SourceIngestion,
    V1CorpusAdapter,
)

CORPUS_NAME = "pmc_oa"
GT_KIND = "xml_full_text"
EXTRACTION_RULES_VERSION = "1"

# Matches ``benchmarks/eval_v1/conventional_metrics._TOKEN_RE`` so
# downstream word-overlap tokenizes the oracle and the parser output
# symmetrically. If that regex changes there, this one MUST change here
# with an EXTRACTION_RULES_VERSION bump.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*|\d+(?:[.,]\d+)*")

_XREF_TAGS = {"xref"}
_EXCLUDED_SECTIONS = {"ref-list", "back"}
_MATHML_TAGS = {"math", "mml:math"}


@dataclass(frozen=True)
class PmcOaAsset:
    """A single acquired PMC-OA article.

    Three paths of provenance, distinct roles:

    - ``metadata_json_path`` — the PMC-authored per-version JSON
      (``<PMCID>.<v>.json``). External, authoritative source of truth
      for identity, license, and version flags. Cheap to read.
    - ``manifest_path`` — our locally-generated provenance receipt
      recording what we downloaded, when, and with what SHA-256s. NOT
      a surrogate for the PMC metadata JSON.
    - ``pdf_path`` / ``xml_path`` — the article bytes themselves.

    ``metadata_json_path`` is optional to keep backward compatibility
    with the v1 (pre-AWS) manifest shape used by the initial test
    fixtures; the AWS pipeline always populates it.
    """

    pmcid: str
    pdf_path: Path
    xml_path: Path
    manifest_path: Path
    version: int | None = None
    metadata_json_path: Path | None = None


@dataclass(frozen=True)
class ExtractionStats:
    """Counters recorded on provenance so exclusions are measurable.

    Every exclusion is countable, not silent. If a downstream reviewer
    asks "were footnotes really preserved?" they can point at the
    counter for the specific PMCID.
    """

    included_paragraphs: int
    included_section_titles: int
    included_table_cells: int
    included_captions: int
    included_footnotes: int
    excluded_ref_list_entries: int
    excluded_xref_markers: int
    equations_with_alttext: int
    equations_with_tex_math: int
    equations_with_text_only: int
    equations_unrepresented: int


def _text(el: ET.Element) -> str:
    return "".join(el.itertext())


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _norm_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _remove_children(el: ET.Element, predicate) -> int:  # type: ignore[no-untyped-def]
    """Detach direct + nested children matching ``predicate`` in place.

    Returns the count removed. Text after the removed element is
    preserved on the parent's ``tail`` so we don't lose intervening
    prose.
    """
    removed = 0
    for parent in list(el.iter()):
        for child in list(parent):
            if predicate(child):
                parent.remove(child)
                removed += 1
    return removed


def _is_math(el: ET.Element) -> bool:
    return _strip_ns(el.tag) == "math"


def _extract_equation_alt(el: ET.Element) -> tuple[str | None, str | None, str | None]:
    """Return ``(alttext, tex_math_text, text_children)`` if present."""
    alttext = el.attrib.get("alttext") or None
    tex_math = None
    for descendant in el.iter():
        if _strip_ns(descendant.tag) == "tex-math":
            tex_math = _norm_whitespace(_text(descendant)) or None
            break
    text_children = _norm_whitespace(_text(el)) or None
    return alttext, tex_math, text_children


class PmcOaV1Adapter(V1CorpusAdapter):
    """PMC-OA adapter — G1 for textual detectors only.

    Layout, span, downstream-QA, and clean-native FPR stages are marked
    ``NOT_APPLICABLE`` with a citation to PROTOCOL_V1.md §2.2.
    """

    def __init__(self, assets: dict[str, PmcOaAsset]) -> None:
        self._assets = dict(assets)

    # --- V1CorpusAdapter contract -----------------------------------

    def capabilities(self) -> CorpusCapabilities:
        na = (
            "PROTOCOL_V1.md §2.2: PMC-OA supplies textual ground truth "
            "only; XML/PDF are separately authored and pixel-perfect "
            "equality is not a valid criterion."
        )
        return CorpusCapabilities(
            corpus_name=CORPUS_NAME,
            on_v1_manifest=True,
            supports_textual_gt=True,
            supports_layout_gt=False,
            supports_clause_span_gt=False,
            supports_downstream_qa_gt=False,
            supports_clean_native_fpr=False,
            not_applicable_reasons={
                "layout_gt": na,
                "clause_span_gt": na,
                "downstream_qa_gt": na,
                "clean_native_fpr": na,
            },
        )

    def acquire(self, doc_id: str) -> Path:
        asset = self._require_asset(doc_id)
        if not asset.pdf_path.exists():
            raise FileNotFoundError(
                f"PMC-OA PDF missing for {doc_id}: {asset.pdf_path}"
            )
        return asset.pdf_path

    def ingest_source(self, doc_id: str) -> SourceIngestion:
        asset = self._require_asset(doc_id)
        manifest = self._read_manifest(asset)
        pdf_meta = manifest["pdf"]
        pdf_bytes = asset.pdf_path.read_bytes()
        actual_sha = _sha256(pdf_bytes)
        if actual_sha != pdf_meta["sha256"]:
            raise RuntimeError(
                f"{doc_id}: PDF on disk hashes {actual_sha} but manifest "
                f"records {pdf_meta['sha256']}; refuse to ingest — the "
                f"cache is inconsistent."
            )
        distribution = manifest.get("distribution", {})
        return SourceIngestion(
            doc_id=doc_id,
            path=asset.pdf_path,
            sha256=actual_sha,
            size_bytes=pdf_meta["size_bytes"],
            media_type="application/pdf",
            provenance={
                "corpus": CORPUS_NAME,
                "source_kind": distribution.get("source", "pmc_oa"),
                "pmcid": asset.pmcid,
                "version": manifest.get("version"),
                "bucket": distribution.get("bucket"),
                "pdf_key": pdf_meta.get("key"),
                "pdf_canonical_url": pdf_meta.get("canonical_url"),
                "pdf_md5": pdf_meta.get("md5"),
                "pdf_etag": pdf_meta.get("etag"),
                "inventory_snapshot_utc": distribution.get("inventory_snapshot_utc"),
                "manifest_path": str(asset.manifest_path),
                "metadata_json_path": (
                    str(asset.metadata_json_path)
                    if asset.metadata_json_path is not None
                    else None
                ),
                "acquired_utc": manifest["acquired_utc"],
            },
        )

    def ingest_ground_truth(self, doc_id: str) -> GroundTruth | None:
        asset = self._require_asset(doc_id)
        manifest = self._read_manifest(asset)
        xml_meta = manifest["xml"]
        xml_bytes = asset.xml_path.read_bytes()
        actual_sha = _sha256(xml_bytes)
        if actual_sha != xml_meta["sha256"]:
            raise RuntimeError(
                f"{doc_id}: XML on disk hashes {actual_sha} but manifest "
                f"records {xml_meta['sha256']}; refuse to ingest — the "
                f"cache is inconsistent."
            )
        data, stats, tables, captions = _transform_jats(xml_bytes)
        # Prefer the canonical PMC metadata JSON as authoritative for
        # license/flags; fall back to the local manifest fields only if
        # metadata_json_path is not available.
        license_from_metadata: str | None = None
        flags_from_metadata: dict[str, bool] | None = None
        if asset.metadata_json_path is not None and asset.metadata_json_path.exists():
            md = json.loads(asset.metadata_json_path.read_text())
            license_from_metadata = md.get("license_code")
            flags_from_metadata = {
                "is_pmc_openaccess": bool(md.get("is_pmc_openaccess", False)),
                "is_retracted": bool(md.get("is_retracted", False)),
                "is_manuscript": bool(md.get("is_manuscript", False)),
                "is_historical_ocr": bool(md.get("is_historical_ocr", False)),
            }
        return GroundTruth(
            doc_id=doc_id,
            kind=GT_KIND,
            data={
                "title": data["title"],
                "abstract": data["abstract"],
                "body_text": data["body_text"],
                "tokens": data["tokens"],
                "tables": tables,
                "captions": captions,
            },
            provenance={
                "corpus": CORPUS_NAME,
                "pmcid": asset.pmcid,
                "version": manifest.get("version"),
                "xml_sha256": actual_sha,
                "xml_key": xml_meta.get("key"),
                "xml_canonical_url": xml_meta.get("canonical_url"),
                "extraction_rules_version": EXTRACTION_RULES_VERSION,
                "citation": manifest.get("citation"),
                "license_code_from_metadata": license_from_metadata,
                "license_from_manifest": manifest.get("license"),
                "flags_from_metadata": flags_from_metadata
                or manifest.get("flags_from_metadata"),
                "extraction_stats": {
                    "included_paragraphs": stats.included_paragraphs,
                    "included_section_titles": stats.included_section_titles,
                    "included_table_cells": stats.included_table_cells,
                    "included_captions": stats.included_captions,
                    "included_footnotes": stats.included_footnotes,
                    "excluded_ref_list_entries": stats.excluded_ref_list_entries,
                    "excluded_xref_markers": stats.excluded_xref_markers,
                    "equations_with_alttext": stats.equations_with_alttext,
                    "equations_with_tex_math": stats.equations_with_tex_math,
                    "equations_with_text_only": stats.equations_with_text_only,
                    "equations_unrepresented": stats.equations_unrepresented,
                },
            },
        )

    def provenance(self) -> dict[str, Any]:
        return {
            "corpus": CORPUS_NAME,
            "extraction_rules_version": EXTRACTION_RULES_VERSION,
            "protocol_section": "PROTOCOL_V1.md §2.2, §2.3, §4.1",
        }

    # --- helpers ----------------------------------------------------

    def _require_asset(self, doc_id: str) -> PmcOaAsset:
        try:
            return self._assets[doc_id]
        except KeyError as e:
            raise KeyError(f"unknown PMC-OA doc_id: {doc_id}") from e

    def _read_manifest(self, asset: PmcOaAsset) -> dict[str, Any]:
        return json.loads(asset.manifest_path.read_text())


# --- pure helpers ---------------------------------------------------


def _sha256(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


def _transform_jats(
    xml_bytes: bytes,
) -> tuple[dict[str, Any], ExtractionStats, list[dict[str, Any]], list[str]]:
    """Extract the primary textual oracle from a JATS document.

    Returns ``(core_data, stats, tables, captions)`` where:

    - ``core_data`` has ``title``, ``abstract``, ``body_text``, ``tokens``.
    - ``stats`` counts included/excluded units for provenance.
    - ``tables`` records per-table cell text with row/column boundaries.
    - ``captions`` lists figure captions in document order.
    """
    root = ET.fromstring(xml_bytes)

    title = _norm_whitespace(_first_text(root, ".//article-meta/title-group/article-title"))
    abstract_parts = [
        _norm_whitespace(_text(p))
        for p in root.findall(".//article-meta/abstract//p")
    ]
    abstract = "\n\n".join(p for p in abstract_parts if p)

    body = root.find(".//body")
    # xref markers are excluded wherever they occur (title, abstract,
    # body, captions, footnotes). Count across the whole tree so the
    # provenance number matches what the renderer actually dropped.
    excluded_xref = sum(1 for el in root.iter() if _strip_ns(el.tag) == "xref")
    excluded_ref_entries = len(root.findall(".//ref-list/ref"))

    body_pieces: list[str] = []
    included_paragraphs = 0
    included_section_titles = 0
    included_table_cells = 0
    included_captions = 0
    included_footnotes = 0
    eq_alttext = eq_texmath = eq_textonly = eq_unrepresented = 0
    tables: list[dict[str, Any]] = []
    captions: list[str] = []

    if body is not None:
        # Walk in document order, emitting extracted text per element type.
        for el in body.iter():
            tag = _strip_ns(el.tag)
            if tag == "title":
                text = _norm_whitespace(_render_inline(el, excluded=_XREF_TAGS))
                if text:
                    body_pieces.append(text)
                    included_section_titles += 1
            elif tag == "p":
                text = _norm_whitespace(_render_inline(el, excluded=_XREF_TAGS))
                if text:
                    body_pieces.append(text)
                    included_paragraphs += 1
            elif tag == "caption":
                text = _norm_whitespace(_render_inline(el, excluded=_XREF_TAGS))
                if text:
                    captions.append(text)
                    body_pieces.append(text)
                    included_captions += 1
            elif tag == "fn":
                text = _norm_whitespace(_render_inline(el, excluded=_XREF_TAGS))
                if text:
                    body_pieces.append(text)
                    included_footnotes += 1
            elif tag == "table-wrap":
                table_record, cell_count = _extract_table(el)
                if table_record["rows"]:
                    tables.append(table_record)
                    included_table_cells += cell_count
                    for row in table_record["rows"]:
                        row_text = " | ".join(row)
                        if row_text.strip():
                            body_pieces.append(row_text)

        # Equation accounting is done on the original tree, not the
        # rendered body_text, so ``equations_unrepresented`` reflects the
        # source rather than the extraction path.
        for el in body.iter():
            if _is_math(el):
                alttext, tex, txt = _extract_equation_alt(el)
                if alttext:
                    eq_alttext += 1
                elif tex:
                    eq_texmath += 1
                elif txt:
                    eq_textonly += 1
                else:
                    eq_unrepresented += 1

    body_text = "\n\n".join(p for p in body_pieces if p)

    # Assemble the canonical token stream (ordered, multiplicity
    # preserved) over title + abstract + body_text. Downstream metrics
    # that want set semantics are responsible for their own reduction.
    corpus_text = "\n\n".join(t for t in (title, abstract, body_text) if t)
    tokens = [t.lower() for t in _TOKEN_RE.findall(corpus_text)]

    stats = ExtractionStats(
        included_paragraphs=included_paragraphs,
        included_section_titles=included_section_titles,
        included_table_cells=included_table_cells,
        included_captions=included_captions,
        included_footnotes=included_footnotes,
        excluded_ref_list_entries=excluded_ref_entries,
        excluded_xref_markers=excluded_xref,
        equations_with_alttext=eq_alttext,
        equations_with_tex_math=eq_texmath,
        equations_with_text_only=eq_textonly,
        equations_unrepresented=eq_unrepresented,
    )
    core = {
        "title": title,
        "abstract": abstract,
        "body_text": body_text,
        "tokens": tokens,
    }
    return core, stats, tables, captions


def _first_text(root: ET.Element, xpath: str) -> str:
    el = root.find(xpath)
    return _text(el) if el is not None else ""


def _render_inline(el: ET.Element, *, excluded: set[str]) -> str:
    """Render an element's text while omitting ``excluded`` child tags.

    Preserves the *referent* content (e.g., footnote body inside ``<fn>``
    handled elsewhere); only the inline marker text is dropped.
    """
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        child_tag = _strip_ns(child.tag)
        if child_tag in excluded:
            # Drop marker text but preserve trailing tail.
            if child.tail:
                parts.append(child.tail)
            continue
        parts.append(_render_inline(child, excluded=excluded))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _extract_table(table_wrap: ET.Element) -> tuple[dict[str, Any], int]:
    """Extract cell text row-by-row with boundaries preserved.

    Returns ``(record, cell_count)`` where ``record`` is
    ``{"label": str, "caption": str, "rows": list[list[str]]}``.
    """
    label = _norm_whitespace(_first_text(table_wrap, ".//label"))
    caption = _norm_whitespace(_first_text(table_wrap, ".//caption"))
    rows: list[list[str]] = []
    cell_count = 0
    for tr in table_wrap.iter():
        if _strip_ns(tr.tag) != "tr":
            continue
        row: list[str] = []
        for td in tr:
            tag = _strip_ns(td.tag)
            if tag not in {"td", "th"}:
                continue
            text = _norm_whitespace(_render_inline(td, excluded=_XREF_TAGS))
            row.append(text)
            cell_count += 1
        if row:
            rows.append(row)
    return {"label": label, "caption": caption, "rows": rows}, cell_count


__all__ = [
    "CORPUS_NAME",
    "EXTRACTION_RULES_VERSION",
    "GT_KIND",
    "ExtractionStats",
    "PmcOaAsset",
    "PmcOaV1Adapter",
]
