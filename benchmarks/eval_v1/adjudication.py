"""Adjudication scaffolding — Authorization A.1c + Appendix B amendment.

Two responsibilities:

1. **Reviewer artifacts.** Prepare a blinded reviewer-facing record for
   each ``(document, parser)`` pair. Reviewer sees the source PDF + the
   normalized extracted markdown + three prewritten questions.

2. **Severity mapping.** Load the mapping file. The default is now
   ``mapping.v1.json`` (frozen; full 64-combination coverage), produced
   by the Appendix B amendment. ``mapping.v0.json`` remains on disk as
   historical evidence of the pre-amendment state (4 diagonal rows only)
   and can be loaded explicitly via ``SeverityMapper.load(path=...)``.

The v1 mapping is derived from the aggregation rule
``label = LABELS[max(rank(q1), rank(q2), rank(q3))]``. Combinations still
not present in whatever mapping file is loaded resolve to
``UNRESOLVED_MAPPING``; ``map()`` never invents a label.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

# The default mapping now points at v1 — the full 64-combination
# frozen mapping introduced by the Appendix B amendment. ``mapping.v0.json``
# is preserved as historical evidence and can still be loaded explicitly
# via ``SeverityMapper.load(path=Path("benchmarks/eval_v1/mapping.v0.json"))``
# to reconstruct what methodology existed prior to that amendment.
MAPPING_FILE = Path(__file__).parent / "mapping.v1.json"
MAPPING_FILE_HISTORICAL_V0 = Path(__file__).parent / "mapping.v0.json"


class SeverityLabel(StrEnum):
    GOOD = "GOOD"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    CATASTROPHIC = "CATASTROPHIC"


UNRESOLVED_MAPPING = "UNRESOLVED_MAPPING"


@dataclass(frozen=True)
class SeverityMapper:
    mapping_id: str
    version: str
    mapping_frozen: bool
    rows: dict[tuple[str, str, str], SeverityLabel]
    q1_values: tuple[str, ...]
    q2_values: tuple[str, ...]
    q3_values: tuple[str, ...]

    @classmethod
    def load(cls, path: Path = MAPPING_FILE) -> SeverityMapper:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows: dict[tuple[str, str, str], SeverityLabel] = {}
        for r in raw["rows"]:
            key = (r["q1"], r["q2"], r["q3"])
            rows[key] = SeverityLabel(r["label"])
        return cls(
            mapping_id=raw["mapping_id"],
            version=raw["version"],
            mapping_frozen=bool(raw.get("mapping_frozen", False)),
            rows=rows,
            q1_values=tuple(raw["q1_values"]),
            q2_values=tuple(raw["q2_values"]),
            q3_values=tuple(raw["q3_values"]),
        )

    def map(self, q1: str, q2: str, q3: str) -> SeverityLabel | str:
        """Return the mapped ``SeverityLabel`` or ``UNRESOLVED_MAPPING``.

        The A.1 discipline: **do not invent a label.** If a reviewer
        submits a combination not in the mapping, the machinery says so
        rather than guess.
        """
        return self.rows.get((q1, q2, q3), UNRESOLVED_MAPPING)

    def all_combinations(self) -> list[tuple[str, str, str]]:
        return [
            (a, b, c)
            for a in self.q1_values
            for b in self.q2_values
            for c in self.q3_values
        ]

    def unresolved_combinations(self) -> list[tuple[str, str, str]]:
        """Combinations the mapping deliberately does not resolve."""
        all_combos = set(self.all_combinations())
        return sorted(all_combos - set(self.rows.keys()))

    def coverage(self) -> dict[str, int]:
        all_combos = self.all_combinations()
        return {
            "total_combinations": len(all_combos),
            "mapped_combinations": len(self.rows),
            "unresolved_combinations": len(all_combos) - len(self.rows),
        }


# --- Reviewer artifact preparation ----------------------------------


REVIEWER_QUESTIONS = {
    "Q1_coverage": (
        "Does the markdown appear to contain substantially all of the "
        "source's textual content? Answer: yes / mostly / partially / no."
    ),
    "Q2_fidelity": (
        "Where content is present, is it recognizably faithful to the "
        "source, or are there stubs, gibberish, or corruptions? Answer: "
        "faithful / minor issues / significant corruption / mostly stub "
        "or junk."
    ),
    "Q3_downstream_usability": (
        "If this markdown were the sole input to a RAG query system for "
        "this document, would answers to typical questions be usable, "
        "degraded, or wrong? Answer: usable / usable-with-caveats / "
        "degraded / wrong."
    ),
}


@dataclass(frozen=True)
class ReviewerArtifact:
    """Blinded reviewer-facing record for one (document, parser) pair.

    Blinding rules (PROTOCOL_V1.md §3.4): reviewer sees neither the
    parser identity nor the AksharaMD score. The parser is hashed; the
    doc is stored under an opaque hash.
    """

    pair_id: str
    blinded_parser_hash: str
    source_pdf_path: str
    extraction_markdown_path: str
    normalized_markdown_path: str
    questions: dict[str, str]
    mapping_reference: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "blinded_parser_hash": self.blinded_parser_hash,
            "source_pdf_path": self.source_pdf_path,
            "extraction_markdown_path": self.extraction_markdown_path,
            "normalized_markdown_path": self.normalized_markdown_path,
            "questions": dict(self.questions),
            "mapping_reference": self.mapping_reference,
            "provenance": dict(self.provenance),
        }


def _blind_parser(parser_name: str) -> str:
    return hashlib.sha256(parser_name.encode("utf-8")).hexdigest()[:16]


def prepare_reviewer_artifact(
    *,
    doc_id: str,
    parser: str,
    source_pdf_path: str,
    extraction_markdown_path: str,
    normalized_markdown_path: str,
    mapping: SeverityMapper,
    extra_provenance: dict[str, Any] | None = None,
) -> ReviewerArtifact:
    pair_hash = hashlib.sha256(f"{doc_id}::{parser}".encode()).hexdigest()[:16]
    return ReviewerArtifact(
        pair_id=pair_hash,
        blinded_parser_hash=_blind_parser(parser),
        source_pdf_path=source_pdf_path,
        extraction_markdown_path=extraction_markdown_path,
        normalized_markdown_path=normalized_markdown_path,
        questions=dict(REVIEWER_QUESTIONS),
        mapping_reference=f"{mapping.mapping_id}::{mapping.version}",
        provenance={
            "blinding_scheme": "sha256(doc_id::parser)[0:16]; parser_name sha256[0:16]",
            "authorization": "A.1c",
            **(extra_provenance or {}),
        },
    )
