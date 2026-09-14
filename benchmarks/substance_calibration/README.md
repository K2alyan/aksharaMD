# Substance-Detector Calibration Corpus

Fixtures for calibrating the upcoming substance-detector portfolio
(P1 placeholder stubs, P2 gibberish, P3 geometric cross-reference).

## Layout

```
benchmarks/substance_calibration/
  corpus.py                       Enumeration API (SubstanceCorpusEntry)
  fixtures/
    placeholder_stubs/            W_PLACEHOLDER_STUB (Phase 1)
      <doc_id>.md                 Parsed markdown fixture
      <doc_id>.json               Ground-truth label
    ocr_gibberish/                W_GIBBERISH (Phase 2)
    encoding_artifacts/           W_ENCODING_ARTIFACTS variants
    dropped_tables/               W_DROPPED_REGION / W_TABLE_GEOMETRY_LOST
      <doc_id>.md                 Parsed markdown fixture
      <doc_id>.pdf                Source PDF (Phase 3 geometric only)
      <doc_id>.json               Ground-truth label
```

## Ground-truth schema

Each fixture Markdown file has a sibling JSON with:

```json
{
  "document_id": "stub_form_pos1",
  "fixture_class": "placeholder_stubs",
  "is_negative": false,
  "expected_rules": ["W_PLACEHOLDER_STUB"],
  "expected_pages": [1],
  "notes": "Human-readable description of what this fixture demonstrates",
  "source_pdf": "stub_form_pos1.pdf"
}
```

- `is_negative`: true for documents that must NOT trigger the detector
  (bounds false-positive rate). Positives have `is_negative: false`.
- `expected_rules`: list of rule_ids the detector should emit. Empty
  list for negatives.
- `expected_pages`: pages where the rule should fire (1-indexed).
- `source_pdf`: optional; only for fixtures whose detector needs the
  source (Phase 3 geometric cross-reference).

## Adding a fixture

1. Create a `.md` file under the appropriate class directory. Its
   content should represent parser output — the extracted markdown as
   the reader would see it, including whatever stub/artifact/gibberish
   the detector should catch (or, for negatives, a clean example).
2. Create a sibling `.json` with the ground-truth schema above.
3. Run `python -m pytest tests/test_substance_calibration_corpus.py -q`
   to verify enumeration picks it up.

## Class-level targets

Once detectors ship, each class should carry at least:

- **3 positives** per class (documents that must trigger the detector)
- **2 negatives** per class (documents that must NOT trigger it, bounding FPR)

Current status: scaffold only. Phase 1/2/3 workers add real fixtures
as their detectors land.
