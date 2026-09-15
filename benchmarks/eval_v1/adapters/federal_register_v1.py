"""V1 corpus adapter for the U.S. Federal Register (naturalistic G2 / FPR baseline).

Role in PROTOCOL_V1 (§2.2, §2.3, §2.4 caveat 3, §4.2–§4.4): Federal
Register is the study's **clean-native, no-failure-expected corpus**.
Its purpose is to bound the false-positive rate of the three new
content-axis detectors (`W_GIBBERISH`, `W_PLACEHOLDER_STUB`,
`W_ENCODING_ARTIFACTS`) and to serve as the G2-adjudication surface
for `W_DROPPED_CONTENT`, `W_TABLE_MISSING`, `W_MULTICOLUMN_ORDER`,
and `W_HEADER_FOOTER_TABLE_GARBLED`.

**Not a G1 text oracle.** Even though clean XML/HTML/plaintext are
distributed alongside the PDF, elevating them to G1 truth would:

1. Violate PROTOCOL_V1 §2.4 caveat 3: any detector fire on an FR
   document must be human-adjudicated, not automatically overruled
   by a rendered text stream.
2. Contradict GPO's own FR-XML User Guide, which states that only the
   PDF and Text versions have legal status as parts of the official
   online format, and that the XML is derived from SGML source and
   documented-lossy on tables.

So the adapter's capability declaration is:
  supports_clean_native_fpr = True
  supports_textual_gt       = False       (with explicit reason)
  supports_layout_gt        = False
  supports_clause_span_gt   = False
  supports_downstream_qa_gt = False

And the ``GroundTruth.data`` payload deliberately does NOT contain
``oracle_text``, ``expected_text``, ``word_set``, or any other field
that could later be mistaken for G1 truth.

Semantic caution on ``GroundTruth.kind = "fpr_baseline"``
---------------------------------------------------------

``fpr_baseline`` names a **corpus-level expectation + human-adjudication
contract**, NOT a document-level truth label. Concretely:

  fpr_baseline ==
      "born-digital naturalistic document
       + no failure assumed a priori
       + every detector fire on this document
         requires G2 human adjudication before being
         counted as an FPR event."

That is not the same as "this document has zero failures." A genuine
weird Federal Register document (say, a rule with actual encoding
artifacts caused by a pipeline glitch) must still be allowed to
produce a true-positive detection. The FPR-baseline framing is a
statistical claim about the corpus population, not a per-document
oracle statement.

Provenance model
----------------

- Strictly offline. Consumes ``FederalRegisterAsset`` records
  populated by ``benchmarks/eval_v1/acquisition/federal_register_api.py``.
  The adapter never opens a socket.
- Three paths of provenance, distinct roles:
    - ``pdf_path`` — GPO-composed authoritative record; parser input.
    - ``xml_path`` — G2 adjudication support only; may be ``None`` at
      the asset level (the acquisition step requires it for prove-one,
      but structurally the type accepts absence so future code can't
      accidentally elevate XML to G1 by depending on it).
    - ``api_json_path`` — snapshot of the FR API's per-document JSON,
      the source of citation / agencies / CFR references / etc.
    - ``manifest_path`` — our local provenance receipt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.corpus_adapter import (
    CorpusCapabilities,
    GroundTruth,
    SourceIngestion,
    V1CorpusAdapter,
)

CORPUS_NAME = "federal_register"
GT_KIND = "fpr_baseline"
EXTRACTION_RULES_VERSION = "1"

# Definition recorded in adapter provenance so the semantics travel
# with the artifact. Chosen over another PROTOCOL amendment per human
# direction (recon §14 decision 10).
NATIVE_AUTHORED_PDF_DEFINITION = (
    "born-digital from the GPO SGML -> typesetting/composition pipeline; "
    "not scanned or OCR-derived. Applies to Federal Register Vol. 60 "
    "(1995) onward per GovInfo FR help. Earlier volumes are digitized "
    "scans and are excluded from B1a by pre-filter volume >= 60."
)


@dataclass(frozen=True)
class FederalRegisterAsset:
    """A single acquired Federal Register document.

    ``xml_path`` is Path | None: XML is G2 adjudication support, not the
    document's definition. Making it structurally optional prevents
    accidental elevation to G1 by any code that starts to depend on it
    unconditionally. The acquisition step still requires XML present
    for prove-one (so the reviewer-support path is exercised
    end-to-end), but the type shape says: "the document is defined by
    the PDF; XML is a support artifact."
    """

    document_number: str
    publication_date: str
    package_id: str
    granule_id: str
    pdf_path: Path
    api_json_path: Path
    manifest_path: Path
    xml_path: Path | None = None


class FederalRegisterV1Adapter(V1CorpusAdapter):
    """Federal Register adapter — G2 clean-native FPR baseline only."""

    def __init__(self, assets: dict[str, FederalRegisterAsset]) -> None:
        self._assets = dict(assets)

    # --- V1CorpusAdapter contract -----------------------------------

    def capabilities(self) -> CorpusCapabilities:
        na_textual = (
            "PROTOCOL_V1.md §2.4 caveat 3: Federal Register is a naturalistic "
            "FPR baseline, NOT a G1 text oracle. Even though clean XML/HTML "
            "are distributed alongside the PDF, GPO's FR-XML User Guide "
            "states that only the PDF and Text versions have legal status "
            "as parts of the official online format and that the XML is "
            "derived from SGML source and documented-lossy on tables. "
            "Elevating XML to G1 would violate both the protocol and GPO's "
            "own semantics."
        )
        na_layout = "Federal Register does not supply layout ground truth."
        na_span = "Federal Register does not supply clause spans."
        na_qa = "Federal Register does not supply QA pairs."
        return CorpusCapabilities(
            corpus_name=CORPUS_NAME,
            on_v1_manifest=True,
            supports_textual_gt=False,
            supports_layout_gt=False,
            supports_clause_span_gt=False,
            supports_downstream_qa_gt=False,
            supports_clean_native_fpr=True,
            not_applicable_reasons={
                "textual_gt": na_textual,
                "layout_gt": na_layout,
                "clause_span_gt": na_span,
                "downstream_qa_gt": na_qa,
            },
        )

    def acquire(self, doc_id: str) -> Path:
        """Return the PDF path — the GPO-composed authoritative record.

        The PDF is the parser input. XML is adjudication support only
        and is never returned as the "source" from this method.
        """
        asset = self._require_asset(doc_id)
        if not asset.pdf_path.exists():
            raise FileNotFoundError(
                f"Federal Register PDF missing for {doc_id}: {asset.pdf_path}"
            )
        return asset.pdf_path

    def ingest_source(self, doc_id: str) -> SourceIngestion:
        """Ingest the authoritative PDF as the parser source.

        The XML hash + path are surfaced on provenance as
        ``adjudication_support`` — parsers do not consume them, but any
        G2 adjudication of a fired warning can consult them.
        """
        asset = self._require_asset(doc_id)
        manifest = self._read_manifest(asset)

        pdf_meta = manifest["pdf"]
        pdf_bytes = asset.pdf_path.read_bytes()
        actual_pdf_sha = _sha256(pdf_bytes)
        if actual_pdf_sha != pdf_meta["sha256"]:
            raise RuntimeError(
                f"{doc_id}: PDF on disk hashes {actual_pdf_sha} but "
                f"manifest records {pdf_meta['sha256']}; refuse to ingest "
                f"— the cache is inconsistent."
            )

        adjudication_support: dict[str, Any] = {}
        xml_meta = manifest.get("xml") or {}
        if asset.xml_path is not None and asset.xml_path.exists() and xml_meta.get("sha256"):
            xml_bytes = asset.xml_path.read_bytes()
            actual_xml_sha = _sha256(xml_bytes)
            if actual_xml_sha != xml_meta["sha256"]:
                raise RuntimeError(
                    f"{doc_id}: XML on disk hashes {actual_xml_sha} but "
                    f"manifest records {xml_meta['sha256']}; refuse to "
                    f"ingest — the adjudication-support artifact has drifted."
                )
            adjudication_support = {
                "xml_sha256": actual_xml_sha,
                "xml_path": str(asset.xml_path),
                "xml_canonical_url": xml_meta.get("canonical_url"),
                "role": "g2_adjudication_support_only",
                "note": xml_meta.get("note", ""),
            }

        distribution = manifest.get("distribution", {})
        provenance = {
            "corpus": CORPUS_NAME,
            "source_kind": distribution.get("source", "federal_register"),
            "source_role": "parser_input_authoritative_record",
            "native_authored_pdf_definition": NATIVE_AUTHORED_PDF_DEFINITION,
            "document_number": asset.document_number,
            "publication_date": asset.publication_date,
            "package_id": asset.package_id,
            "granule_id": asset.granule_id,
            "citation": manifest.get("citation"),
            "volume": manifest.get("volume"),
            "type": manifest.get("type"),
            "start_page": manifest.get("start_page"),
            "end_page": manifest.get("end_page"),
            "page_length": manifest.get("page_length"),
            "pdf_sha256": actual_pdf_sha,
            "pdf_canonical_url": pdf_meta.get("canonical_url"),
            "adjudication_support": adjudication_support,
            "population_publication_date_lte": distribution.get(
                "population_publication_date_lte"
            ),
            "population_publication_date_gte": distribution.get(
                "population_publication_date_gte"
            ),
            "manifest_path": str(asset.manifest_path),
            "api_json_path": str(asset.api_json_path),
            "acquired_utc": manifest["acquired_utc"],
        }
        return SourceIngestion(
            doc_id=doc_id,
            path=asset.pdf_path,
            sha256=actual_pdf_sha,
            size_bytes=pdf_meta["size_bytes"],
            media_type="application/pdf",
            provenance=provenance,
        )

    def ingest_ground_truth(self, doc_id: str) -> GroundTruth | None:
        """Return the corpus-level FPR-baseline contract for this document.

        The payload is DELIBERATELY LEAN: no `oracle_text`, no
        `expected_text`, no `word_set`, no per-token expectation of
        any kind. `fpr_baseline` is a corpus contract (see module
        docstring), not a document-level truth label.
        """
        asset = self._require_asset(doc_id)
        manifest = self._read_manifest(asset)

        # Adjudication-support summary — surfaces the XML hash + role
        # note so downstream reviewers know what to consult, without
        # ever using XML as G1 truth.
        xml_meta = manifest.get("xml") or {}
        adjudication_support: dict[str, Any] = {}
        if asset.xml_path is not None and xml_meta.get("sha256"):
            adjudication_support = {
                "xml_sha256": xml_meta["sha256"],
                "xml_path": str(asset.xml_path),
                "xml_canonical_url": xml_meta.get("canonical_url"),
                "role": "g2_adjudication_support_only",
                "note": xml_meta.get("note", ""),
            }

        return GroundTruth(
            doc_id=doc_id,
            kind=GT_KIND,
            data={
                "document_number": asset.document_number,
                "publication_date": asset.publication_date,
                "package_id": asset.package_id,
                "granule_id": asset.granule_id,
                "citation": manifest.get("citation"),
                "agencies": manifest.get("agencies", []),
                "type": manifest.get("type"),
                "volume": manifest.get("volume"),
                "start_page": manifest.get("start_page"),
                "end_page": manifest.get("end_page"),
                "page_length": manifest.get("page_length"),
                "adjudication_support": adjudication_support,
                "contract": {
                    "kind": "fpr_baseline",
                    "meaning": (
                        "corpus-level expectation + human-adjudication "
                        "contract, NOT a document-level truth label. "
                        "Detector fires on this document require G2 "
                        "human adjudication before counting as FPR "
                        "events; genuine failures are still allowed to "
                        "be true positives (see PROTOCOL_V1 §2.4 caveat 3)."
                    ),
                    "native_authored_pdf_definition": (
                        NATIVE_AUTHORED_PDF_DEFINITION
                    ),
                },
                # NOTE: intentionally NO 'oracle_text', 'expected_text',
                # 'expected_prose', or 'word_set' — those would signal
                # G1 semantics we explicitly do not support.
            },
            provenance={
                "corpus": CORPUS_NAME,
                "document_number": asset.document_number,
                "publication_date": asset.publication_date,
                "package_id": asset.package_id,
                "granule_id": asset.granule_id,
                "extraction_rules_version": EXTRACTION_RULES_VERSION,
                "api_json_sha256": manifest["api_json"]["sha256"],
                "pdf_sha256": manifest["pdf"]["sha256"],
                "xml_sha256": xml_meta.get("sha256"),
                "population_publication_date_lte": manifest["distribution"][
                    "population_publication_date_lte"
                ],
                "population_publication_date_gte": manifest["distribution"][
                    "population_publication_date_gte"
                ],
                "protocol_section": (
                    "PROTOCOL_V1.md §2.2, §2.3, §2.4 caveat 3, §4.2–§4.4"
                ),
            },
        )

    def provenance(self) -> dict[str, Any]:
        return {
            "corpus": CORPUS_NAME,
            "extraction_rules_version": EXTRACTION_RULES_VERSION,
            "role": "clean_native_fpr_baseline",
            "native_authored_pdf_definition": NATIVE_AUTHORED_PDF_DEFINITION,
            "protocol_section": (
                "PROTOCOL_V1.md §2.2, §2.3, §2.4 caveat 3, §4.2–§4.4"
            ),
            "no_g1_text_oracle": (
                "XML/HTML/plaintext are G2-adjudication support only. "
                "Adapter emits no oracle_text, expected_text, or word_set "
                "fields; capability supports_textual_gt is False."
            ),
        }

    # --- helpers ----------------------------------------------------

    def _require_asset(self, doc_id: str) -> FederalRegisterAsset:
        try:
            return self._assets[doc_id]
        except KeyError as e:
            raise KeyError(f"unknown Federal Register doc_id: {doc_id}") from e

    def _read_manifest(self, asset: FederalRegisterAsset) -> dict[str, Any]:
        return json.loads(asset.manifest_path.read_text())


def _sha256(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


__all__ = [
    "CORPUS_NAME",
    "EXTRACTION_RULES_VERSION",
    "GT_KIND",
    "NATIVE_AUTHORED_PDF_DEFINITION",
    "FederalRegisterAsset",
    "FederalRegisterV1Adapter",
]
