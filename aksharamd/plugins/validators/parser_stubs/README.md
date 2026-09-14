# Parser Stub Catalog

Per-parser fingerprint catalogs consumed by `W_PLACEHOLDER_STUB`
(`PlaceholderStubValidator`). Each JSON file lists known extraction-stub
patterns emitted by one specific parser — regex or literal strings that
almost always indicate content the parser could not extract.

## Purpose

Different parsers have different failure signatures. When Docling can't
extract an image, it might emit `<image_placeholder>`; MarkItDown emits
`![Image](...)`; MinerU emits `[image_omitted]`. A single hardcoded list
inside the validator would either bloat (all parsers' stubs) or
under-catch (only one parser's stubs).

The catalog splits the fingerprint sets per parser. At validation time,
`PlaceholderStubValidator`:

1. Always loads the **`common.json`** baseline (parser-agnostic stubs
   present in every catalog).
2. Also loads the parser-specific catalog if `ctx.parser_name` matches
   one of the known parsers (e.g. `reference`, `marker`, `docling`,
   `markitdown`, `mineru`).
3. Applies all loaded patterns as extra literal-string triggers for the
   detector's Trigger B (extraction stubs).

If `ctx.parser_name` is unknown or unset, only the common baseline
applies.

## Schema

Each JSON file is an object of shape:

```json
{
  "parser_id": "docling",
  "parser_notes": "IBM Docling v2.x — layout-aware VLM parser",
  "stubs": [
    {
      "pattern": "<image_placeholder>",
      "type": "literal",
      "notes": "Emitted when the image extractor fails",
      "first_seen_version": "2.10"
    }
  ]
}
```

Required fields per catalog:
- `parser_id` — must match `ctx.parser_name` (lowercase, underscore-safe)
- `stubs` — list of stub entries

Optional:
- `parser_notes` — one-line human description
- `catalog_version` — string, bumped when stubs added/removed

Required fields per stub entry:
- `pattern` — the literal or regex to match (case-insensitive by default)
- `type` — `"literal"` or `"regex"`

Optional per stub:
- `notes` — human context
- `first_seen_version` — parser version where the stub was first observed
- `dropped_in_version` — parser version where the stub was fixed (if any)

## License

The catalogs are published under **MIT** to allow community contribution
without triggering the main repo's PolyForm Noncommercial license. See
`LICENSE-parser-stubs.md` in this directory.

## Current catalogs

Present today:
- `common.json` — parser-agnostic baseline (mirrors the hardcoded fallback in `placeholder_stub.py`)
- `reference.json` — AksharaMD's bundled PyMuPDF-based parser (starts empty; populated as usage data arrives)
- `marker.json` — [marker](https://github.com/VikParuchuri/marker) (starts with well-known stubs)
- `docling.json` — [Docling](https://github.com/docling-project/docling) (starts with well-known stubs)
- `markitdown.json` — [MarkItDown](https://github.com/microsoft/markitdown) (starts with well-known stubs)
- `mineru.json` — [MinerU](https://github.com/opendatalab/MinerU) (starts with well-known stubs)

Most catalogs beyond `common.json` and `marker.json` ship with **zero
stubs today** — the scaffold is the important thing. Contributors add
observed stubs as they encounter them.

## Adding a stub

1. Observe a stub pattern in a parser's output.
2. Add an entry to the appropriate `<parser>.json` file under `stubs`.
3. Run `python -m pytest tests/test_placeholder_stub_validator.py -q` — the schema-conformance test should still pass.
4. Send a PR. Because this directory is MIT-licensed, contributions do
   not require signing the PolyForm Noncommercial contributor agreement
   used elsewhere in the repo.
