# Federal Register Reconnaissance (B1a-4)

**Status:** Read-only reconnaissance. No acquisition code has been written. No documents downloaded. Awaiting human approval of the acquisition contract before B1a-4 resumes.

**Date:** 2026-09-14 (endpoints verified live via API responses).

**Reason for reconnaissance:** Federal Register is B1a's **naturalistic G2 / FPR-baseline** corpus. Its role is to answer *"does AksharaMD fire on documents encountered in the wild, from a source known to be clean/native?"* It is **NOT a G1 text oracle** — clean HTML/XML availability must not tempt the implementation into treating text as ground truth. The protocol's §2.4 caveat 3 is explicit: *"if a detector fires on a Federal Register document, the finding must be human-adjudicated before it is counted as an FPR event. It might be a genuine detection."*

**Role in PROTOCOL_V1** (§2.2, §2.3, §2.4 caveat 3, §4.2–§4.4):
- G2 FPR-baseline for `W_DROPPED_CONTENT`, `W_GIBBERISH`, `W_PLACEHOLDER_STUB`, `W_ENCODING_ARTIFACTS`, `W_TABLE_MISSING`, `W_MULTICOLUMN_ORDER`, `W_HEADER_FOOTER_TABLE_GARBLED`.
- Predeclared threshold (§4.2, §12.2): `FPR ≤ 0.05` on the three new content-axis detectors combined.
- Detector firings must be human-adjudicated before counting as FPR events.

---

## 1. Canonical distribution channels

Two independent, unauthenticated live channels as of 2026-09-14:

- **Federal Register public API v1** — `https://www.federalregister.gov/api/v1/`. JSON per document + list endpoints; no key required. Returns per-document metadata plus links to the authoritative PDF, `full_text_xml_url`, `body_html_url`, `raw_text_url`, `mods_url`. Source: [FR API docs](https://www.federalregister.gov/developers/documentation/api/v1).
- **GovInfo (GPO) bulk repo** — `https://www.govinfo.gov/bulkdata/FR/{YYYY}/{MM}/FR-YYYY-MM-DD.xml` plus per-year / per-month zips. Coverage: bulk XML from 2000, PDF from Vol. 60 (1995), digitized-scanned PDFs from Vol. 1 (1936). Source: [GovInfo FR help](https://www.govinfo.gov/help/fr).

Also live but not primary:
- Per-document permalinks on `federalregister.gov/documents/{YYYY}/{MM}/{DD}/{document_number}/{slug}` — the reader view.
- Legacy `fdsys.gpo.gov/*` URLs redirect into GovInfo; not a distinct source.

**Live constraint on the reader-view host:** `www.federalregister.gov` fingerprints non-browser clients and 302-redirects them to a bot challenge on `unblock.federalregister.gov`. Observed live during recon on `/developers/…` and `/reader-aids/…`. **The API host (`/api/v1/…`) itself does not block** for well-formed requests with a real `User-Agent`. Acquisition must hit the API host, not the reader host.

## 2. Artifact set per document

Per FR API response and [rOpenGov FR field reference](https://cran.r-project.org/web/packages/federalregister/federalregister.pdf), each document surfaces:

| Field | Content |
|---|---|
| `pdf_url` | GPO-produced PDF — issue-level, page-anchored to the doc |
| `full_text_xml_url` | Per-document XML rendition |
| `body_html_url` | HTML rendering of doc body |
| `raw_text_url` | Plain text extraction |
| `mods_url` | MODS descriptive metadata (via GovInfo) |
| `public_inspection_pdf_url` | Pre-publication PDF (~1 day before official) |
| `html_url` | The FR reader-view permalink |

At the GovInfo layer, each FR document is a **granule** inside a per-issue package (`FR-YYYY-MM-DD`). PREMIS preservation metadata exists per-package.

## 3. G1 vs G2 boundary — the load-bearing finding

GPO's own FR-XML User Guide is unusually explicit here. Direct quote ([FR-XML User Guide, usgpo/bulk-data](https://github.com/usgpo/bulk-data/blob/main/FR-XML_User-Guide.md)):

> "Only the PDF and Text versions of Federal Register content on GPO Access and FDsys have legal status as parts of the official online format of the Federal Register."

And:

> "The XML-structured files are derived from SGML-tagged data and printing codes."

Combined with the same guide's warning that "complex tabular material in XML files may not display as correctly composed objects equivalent to the tables that appear in the Text and PDF files."

**Mapping onto our protocol tiers:**

| Artifact | Category | AksharaMD role |
|---|---|---|
| `pdf_url` (PDF) | **(a) authoritative published record** | **Parser source** for B1a-4 |
| `raw_text_url` / Text | **(a) authoritative published record** | Not used as parser source; also NOT the G1 text oracle for FR |
| `full_text_xml_url` (XML) | (b) derived rendering (from SGML source) | G2-adjudication support only |
| `body_html_url` (HTML) | (b) derived rendering (from XML) | G2-adjudication support only |
| MODS / PREMIS | (c) metadata layer | Provenance receipts |
| API JSON | (c) metadata layer | Identity + fetching |

**Temptation zone.** `raw_text_url` and `full_text_xml_url` are trivially clean and easy to fetch. The temptation is to use one of them as a G1 text oracle ("we have clean prose; why not compare parser output against it?"). Two reasons that is wrong here:

1. **Protocol design.** The corpus's role is naturalistic G2/FPR. Promoting FR to G1 would violate §2.4 caveat 3 and would silently reclassify a naturalistic-baseline claim as an oracle-grounded claim.
2. **GPO's own semantics.** The XML is *derived* from SGML source and is documented-lossy on tables. Even setting the protocol aside, XML is not the authoritative record — PDF/Text are. Elevating a derived artifact above the authoritative one would be scientifically indefensible.

**Recommendation, locked:** treat XML/HTML/plaintext as G2-adjudication support only. If a detector fires on an FR document, adjudicators may consult those artifacts, but no adapter path emits them as G1 ground truth. The adapter must **not** expose an `oracle_text` or `expected_text` field on its `GroundTruth.data`; capability declaration must set `supports_textual_gt=False`.

## 4. Identity model

The FR API and GovInfo use **different** primary keys — both must be recorded per document.

**FR API side:**
- `document_number` — OFR-assigned FR Doc Number. Format is **not monotonic**: older docs use `E9-20836`, recent use `2024-31234`. Do not parse for ordering.
- `citation` — e.g., `"89 FR 12345"`
- `publication_date`
- `agencies` — list of structured objects (`raw_name`, `name`, `id`, `slug`), **not strings**
- `type` — one of `RULE`, `PRORULE`, `NOTICE`, `PRESDOCU`
- `executive_order_number` (nullable)
- `cfr_references`
- `docket_id`
- `start_page`, `end_page`, `volume`

**GovInfo side:**
- `packageId` — `FR-YYYY-MM-DD` (the issue)
- `granuleId` — per-document identifier (older: `E9-20836`; recent: FR doc number)
- Detail URL: `https://www.govinfo.gov/app/details/{packageId}/{granuleId}`

**Fixity:** GovInfo publishes PREMIS metadata with checksums for the whole package. The FR API does not expose per-file hashes — the adapter must compute + store SHA-256 for both the PDF and the XML at fetch time.

**Recommended composite key for the local manifest:**
```
(document_number, publication_date, packageId, granuleId, pdf_sha256)
```
Primary display: `document_number`. Cross-verification: `packageId` + `granuleId`. Integrity: `pdf_sha256`.

## 5. Bulk enumeration / population

**FR API** supports `conditions[publication_date][year]=YYYY`, `[gte]=YYYY-MM-DD`, `[lte]=YYYY-MM-DD`, `[is]=…`, plus `conditions[type][]`, `conditions[agencies][]`, `per_page` (max 1000), cursor pagination. This is our enumeration surface.

**GovInfo** publishes daily XML files (`FR-YYYY-MM-DD.xml`, weekdays minus federal holidays — ~250 files/year), monthly and annual ZIPs. Annual XML zip ≈ 100 MB.

**Population scale:** FR 2024 was 106,109 pages, 3,248 final rules, plus tens of thousands of notices/proposed rules/presidential documents. Total ~35–45k documents/year. PDF-inclusive corpus is multi-GB. Source: [CEI 10kc 2025 report](https://cei.org/publication/10kc-2025-numbers-of-rules/).

For a timestamped "as of T" population: pin a `publication_date` closed range, resolve via API, verify each doc exists in the corresponding `FR-{date}.xml` on GovInfo.

**Publication-lag caveat for reproducibility:** documents appear first on Public Inspection (`public_inspection_pdf_url`), then officially publish the next business day. `full_text_xml_url` and GovInfo issue XML may lag another business day beyond `pdf_url`. For deterministic replay, fetch only documents where `publication_date ≤ T − 2 business days`.

## 6. Prove-one path — smallest bandwidth

Given one `document_number` selected deterministically:

1. `GET https://www.federalregister.gov/api/v1/documents/{document_number}.json` — 5–30 KB metadata + all artifact URLs.
2. `GET {pdf_url}` — authoritative PDF (typical 50 KB – 2 MB, larger for long rules).
3. `GET {full_text_xml_url}` — per-doc XML for G2-adjudication support (10–200 KB). Recorded but not treated as oracle.
4. Optional: `GET https://www.govinfo.gov/metadata/pkg/{packageId}/granules/{granuleId}/mods.xml` — MODS metadata (5–20 KB) for identity cross-check.

Total: ~4 GETs, < 5 MB for a typical document.

## 7. License / public domain

Federal Register content is a "work of the United States Government" under **17 USC §105** → not eligible for copyright, public domain by statute. Confirmed for both PDF and XML/HTML — both are produced by GPO/OFR in official duties. Sources: [17 USC 105](https://uscode.house.gov/view.xhtml?req=%28title%3A17+section%3A105+edition%3Aprelim%29), [Wikipedia: US federal government works copyright status](https://en.wikipedia.org/wiki/Copyright_status_of_works_by_the_federal_government_of_the_United_States).

Exception to note (not a practical blocker but worth recording in provenance): third-party materials **incorporated by reference** (e.g., IEEE/ISO standards cited by rules) may retain copyright, but they appear only as citations in FR text, not as embedded content.

## 8. Rate limits / authentication

- **FR API:** no key. No documented per-hour cap; "reasonable use" only. Adapter must self-throttle (~1–2 req/s) and set a descriptive `User-Agent` (include contact email per GPO/SEC convention).
- **GovInfo bulk (`/bulkdata/…`) + metadata (`/metadata/pkg/…`):** unauthenticated.
- **GovInfo Data API (`api.govinfo.gov`):** would require an `api.data.gov` key at 1000 req/hour — **not needed** for our adapter path; bulk/metadata suffice.

## 9. "Native-authored PDF" reality check

The protocol's §2.2 phrasing ("native-authored PDFs") could mislead a reader into "hand-crafted in Acrobat." **The actual reality (confirmed via GPO's own FR-XML User Guide):** agencies submit SGML-tagged source with printing codes → GPO's typesetting/composition pipeline → **PDF is the primary composed output; XML is derived from the same SGML source**. GPO then digitally signs each PDF (blue-ribbon Authenticated PDF program).

So "native-authored" as used in our protocol should be read as **"born-digital from a government typesetting pipeline, never OCR'd, never scanned."** The relevant failure modes are composition/typesetting pipeline artifacts (table renderer edge cases, glyph substitutions), not scanner/OCR artifacts. Detectors calibrated on OCR-derived garbage should not fire on modern FR PDFs; if they do, that is exactly the FPR event §4.2 wants to catch.

Sources: [FR-XML User Guide](https://github.com/usgpo/bulk-data/blob/main/FR-XML_User-Guide.md), [GPO Authentication Overview PDF](https://www.govinfo.gov/media/authenticationoverview.pdf).

## 10. Naturalistic sampling / selection concerns

- **Type distribution is highly skewed.** 2024: 3,248 final rules vs tens of thousands of `NOTICE` items. A uniform-random draw is dominated by notices. Stratify by `type` for a representative pilot.
- **`PRESDOCU` (Presidential documents)** — executive orders, proclamations, memoranda — are short, stylistically atypical, often only 1–2 pages. Analogous to `is_historical_ocr` for PMC-OA: worth flagging and probably excluding from prove-one or handling as a separate stratum.
- **Agency skew:** a small handful of agencies (Coast Guard, EPA, FAA, HHS) dominate rule counts.
- **OCR-derived text — HARD FILTER.** From [GovInfo FR help](https://www.govinfo.gov/help/fr): **Vol. 59 (1994) and earlier are digitized scans**, so their PDFs are OCR-derived, not born-digital. Including them would confound the FPR baseline (they'd exhibit OCR artifacts). **Recommend `volume >= 60` as a required pre-filter.** This is the FR analog of PMC-OA's `is_historical_ocr == false`.

## 11. SEC EDGAR — scope call-out (deferred)

Protocol §2.2 groups "Federal Register / SEC filings." Recon recommendation: **use Federal Register alone for B1a-4.** Reasoning:

- EDGAR primary format is HTML with inline XBRL, plus separate XML/JSON. Native PDF filings exist but are inconsistent — many filings have no PDF at all. That undermines EDGAR's usefulness as an FPR-baseline PDF corpus for our purposes.
- EDGAR has stricter operational constraints (10 req/s hard cap, mandatory User-Agent; 403/429 on violation).
- EDGAR's document diversity (10-K exhibits, S-1 prospectuses, 8-K attachments) is much wider than FR's stable ~15 subtype list — harder to bound a naturalistic sample.

**Recommendation:** defer EDGAR to a later milestone if we ever want a *second* naturalistic corpus (e.g., "does baseline behavior transfer across pipelines"). Treat it as its own adapter with HTML+XBRL as the primary artifact. No B1a-4 work.

## 12. Things that will trip a first-time adapter author

1. **Bot-fingerprint 302 on `www.federalregister.gov`** — non-browser clients get bounced to `unblock.federalregister.gov`. Observed live during recon. **The `/api/v1/…` host does not block**; adapter must hit the API host and set a real `User-Agent` with a contact email.
2. **~24-hour publication lag.** Docs land on Public Inspection first, then official publication the next business day. Don't fetch same-day; use `publication_date ≤ T − 2`.
3. **GovInfo XML is issue-level, not per-document.** Per-doc XML is only available via the FR API. If the adapter wanted per-doc via GovInfo, it would have to split `FR-YYYY-MM-DD.xml` on granule boundaries — non-trivial. Use the API for per-doc.
4. **Table fidelity in XML is documented-lossy** per GPO. Do not use XML-extracted table text as ground truth.
5. **No CORS on FR API** — server-side calls only.
6. **Pagination scheme evolves** — historically switched from page-based to cursor-based. Handle both `next_page_url` and `total_pages`.
7. **`document_number` format is not monotonic** — old: `E9-20836`, new: `2024-31234`. Do not parse it for ordering. Use `SHA-256(document_number)` as the deterministic ordering key (same primitive we use elsewhere).
8. **`agencies` is a list of structured objects**, not strings.
9. **Issue calendar is weekday-only minus federal holidays** — 20 issues in January 2025, not 22. Iteration must tolerate absent dates.
10. **Volume boundary:** `volume >= 60` for born-digital PDFs; earlier issues are OCR'd scans and would confound the FPR baseline.

---

## Preliminary adapter shape (proposed, NOT implemented)

```python
@dataclass(frozen=True)
class FederalRegisterAsset:
    document_number: str        # primary display key
    publication_date: str       # ISO 8601
    package_id: str             # GovInfo FR-YYYY-MM-DD
    granule_id: str             # GovInfo granule
    pdf_path: Path              # parser source
    xml_path: Path              # G2-adjudication support only
    api_json_path: Path         # FR API JSON snapshot
    manifest_path: Path         # our local provenance receipt
```

Capabilities: `supports_layout_gt=False`, `supports_textual_gt=False` (explicitly — the XML is not a G1 oracle here), `supports_clause_span_gt=False`, `supports_downstream_qa_gt=False`, `supports_clean_native_fpr=True`. `not_applicable_reasons` for the four false capabilities cite §2.2 + §2.4 caveat 3 explicitly for `textual_gt` (emphasizing that clean XML availability does NOT elevate FR to G1).

Ground-truth `kind` proposal: `"fpr_baseline"` (new). Payload:

```
{
  "document_number": str,
  "publication_date": str,
  "package_id": str,
  "granule_id": str,
  "citation": str,
  "agencies": [{"raw_name", "name", "id", "slug"}, ...],
  "type": "RULE" | "PRORULE" | "NOTICE" | "PRESDOCU",
  "volume": int,
  "start_page": int, "end_page": int,
  "adjudication_support": {
    "xml_sha256": str,
    "xml_path": str,
    "note": "XML is derived from SGML source per GPO FR-XML User Guide; documented-lossy on tables. Use for G2 adjudication only, never as G1 oracle."
  }
}
```

**Deliberate absences from the payload:** no `oracle_text` field, no `expected_prose`, no `word_set` — these would signal G1 semantics we explicitly do not support.

## 13. What has NOT been touched

- No acquisition code written.
- No FR documents downloaded.
- No changes to `PROTOCOL_V1.md`.
- No commits made.
- No changes to `benchmarks/eval_v1/adapters/__init__.py`.
- No memory updates.
- No B1a-5 selection work.

**STOP for human review.**

## 14. Decisions requested before B1a-4 acquisition begins

1. **Distribution channel.** Approve FR API v1 (`api/v1/documents.json`) for enumeration + per-doc metadata, with GovInfo `packageId`/`granuleId` recorded as cross-check identifiers?

2. **Parser source.** Approve `pdf_url`-fetched PDF as the parser source, matching the PDF/Text authoritative-record semantics from GPO's own docs?

3. **XML disposition.** Approve treating `full_text_xml_url` strictly as **G2 adjudication support**, never a G1 oracle? Adapter's capability declaration would set `supports_textual_gt=False` and its `not_applicable_reasons["textual_gt"]` would cite §2.4 caveat 3 + GPO's own guidance that XML is derived from SGML and non-authoritative.

4. **Volume gate.** Approve `volume >= 60` (1995 onward) as a required pre-filter — the analog of PMC-OA's `is_historical_ocr == false`. Earlier issues are OCR'd scans and would confound the FPR baseline.

5. **Type stratification for prove-one.** Recommend excluding `PRESDOCU` (Presidential documents) from the prove-one pool — short, stylistically atypical. For the pilot's 6-document scale, propose stratifying across `RULE`, `PRORULE`, `NOTICE`. Refine?

6. **Deterministic candidate ordering.** For prove-one, propose `SHA-256(document_number)` ascending over an FR API query for a pinned publication-date range. Confirm ordering primitive.

7. **Publication-lag safety.** Propose enforcing `publication_date ≤ T − 2 business days` at query time so `full_text_xml_url` and GovInfo XML are guaranteed present. Confirm.

8. **Scope on SEC EDGAR.** Confirm B1a-4 covers Federal Register alone; SEC EDGAR deferred (its inclusion in protocol §2.2's "Federal Register / SEC filings" phrasing becomes an explicit "V-later" note in the acquisition contract).

9. **Ground-truth `kind` string.** Propose `"fpr_baseline"` as a new `GroundTruth.kind` (adds one recognized value alongside `"xml_full_text"` from PMC-OA and `"bbox_layout"` from DocLayNet). Refine or reject?

10. **Optional protocol tightening.** The protocol phrase "native-authored PDFs" is technically accurate but easy to misread as "hand-typed in Acrobat." Recon §9 shows the actual reality is "born-digital from GPO's SGML→typesetting pipeline." Worth a tiny protocol wording clarification in the same style as PR #179, or leave as-is and just record the definition in `FederalRegisterV1Adapter.provenance()`? Recommend: leave protocol wording alone; put a definition in the adapter provenance so the semantics travel with the artifact.
