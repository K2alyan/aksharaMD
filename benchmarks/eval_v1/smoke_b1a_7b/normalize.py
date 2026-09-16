"""Normalization stage — normalization_version = 2.

Deliberately minimal:

- Unicode NFC.
- Whitespace canonicalization: strip trailing whitespace on each
  line; collapse mixed line endings to LF; ensure the file ends with
  exactly one newline (or is exactly empty).

**Must not:**
- Rewrite tables, headings, or list markers.
- Infer structure the parser did not produce.
- Replace glyphs beyond NFC normalization.

Empty markdown input produces empty markdown output (per Docling
empty-output-on-success behavior; the smoke observes this without
special-casing it here).
"""
from __future__ import annotations

import unicodedata

NORMALIZATION_VERSION = "2"


def normalize(markdown: str) -> str:
    if markdown == "":
        return ""
    nfc = unicodedata.normalize("NFC", markdown)
    # Canonicalize line endings.
    lines = nfc.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Strip trailing whitespace per line.
    lines = [ln.rstrip() for ln in lines]
    # Drop trailing empty lines then re-add exactly one newline at end.
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return ""
    return "\n".join(lines) + "\n"
