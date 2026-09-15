"""B1a-5a pilot selector — deterministic dry selection over frozen populations.

Locked anchors (2026-09-14):

- **PMC-OA population:** the ``pmc-oa-opendata`` inventory snapshot
  ``2026-09-01T01-00Z`` (verified live 2026-09-14). Manifest + shard
  hashes recorded to the population snapshot; if the snapshot has
  aged out of PMC's 30-day retention, the selector STOPS rather than
  silently choosing another date.
- **DocLayNet population:** ``docling-project/DocLayNet-v1.2`` at
  revision ``0daf93102e2efce76c3e11a274a5e0d0969391d3``, ``split=train``,
  **all 72 shards**. Lightweight columns only (metadata + modalities +
  category_id + area + bboxes). NO image/PDF payload columns.
- **Federal Register population:** literal publication_date interval
  ``[2025-01-01, 2025-12-31]`` (completed calendar year). NO
  ``compute_safe_cutoff()`` — a completed historical year sidesteps
  the weekend/federal-holiday issue by construction.

Selection algorithm (v1):

1. Enumerate the population from the frozen snapshot (metadata only).
2. Apply declared eligibility filters.
3. Remove IDs in the union of relevant exclusion ledgers.
4. Deterministic DEV assignment via
   :func:`benchmarks.eval_v1.corpus_split.assign_partition`.
5. Within DEV, rank by ``SHA-256(canonical_id)`` ascending.
6. Take the first N from the DEV-ranked list (per FR stratum, take
   the first 2 per stratum).

Invariant: given identical frozen inputs and
``SELECTION_ALGORITHM_VERSION``, the selected canonical-ID set and
ordering MUST be byte-identical.

Corpus canonical IDs:
- PMC-OA: ``"{PMCID}.{version}"``
- DocLayNet: ``page_hash``
- Federal Register: ``document_number``

Contamination clarification (per human, 2026-09-14):
- Population eligibility scan reading only predeclared metadata
  columns is NOT contamination.
- Human inspection of candidate content IS contamination.
- PNG/PDF/JATS payload retrieval IS contamination.
- Prove-one-fetched IDs are already contaminated and appear in each
  corpus's exclusion ledger.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.corpus_split import Partition, assign_partition

SELECTION_ALGORITHM_VERSION = "1"

# Locked target allocation for B1.
PMC_OA_TARGET_N = 8
DOCLAYNET_TARGET_N = 6
FEDERAL_REGISTER_TARGET_N = 6
FR_STRATA = {"Rule": 2, "Proposed Rule": 2, "Notice": 2}
FR_STRATUM_TARGET = 2
FR_TOTAL_TARGET = 6


class SelectionError(RuntimeError):
    """Raised on any locked-invariant violation during dry selection."""


class InsufficientEligibleError(SelectionError):
    """Raised when a corpus does not yield N eligible DEV candidates.

    Per the human's hard rule: do NOT relax eligibility, change the
    hash partition, reach into CAL/held-out, or substitute another
    corpus. STOP and surface the shortfall.
    """


@dataclass(frozen=True)
class Candidate:
    """One candidate row in the frozen population.

    ``canonical_id`` is the identifier fed to :func:`assign_partition`
    AND to the SHA-256 within-DEV rank. Corpus adapters MUST agree on
    what constitutes canonical identity — see module docstring.
    """

    canonical_id: str
    partition_hash_hex: str  # SHA-256(canonical_id) hex — the ordering key
    metadata: dict[str, Any]
    corpus: str


@dataclass(frozen=True)
class EligibilityDecision:
    ok: bool
    reason: str | None


@dataclass(frozen=True)
class ExclusionEntry:
    canonical_id: str
    corpus: str
    reason: str
    source: str  # ledger file path or "prove-one" or "A/A.1"


@dataclass
class CorpusOutcome:
    corpus: str
    n_enumerated: int = 0
    # For eager-materialization corpora (DocLayNet, Federal Register)
    # these three are honest population counts. For a lazy-eval corpus
    # (PMC-OA rank-walk) they are None: the full eligible-DEV
    # population was never materialized. See the lazy fields below.
    n_eligible: int | None = 0
    n_after_exclusion: int | None = 0
    n_in_dev: int | None = 0
    n_selected: int = 0
    selected: list[dict[str, Any]] = field(default_factory=list)
    excluded_by_ledger: list[dict[str, Any]] = field(default_factory=list)
    ineligible_head: list[dict[str, Any]] = field(default_factory=list)
    dev_rank_neighbors: list[dict[str, Any]] = field(default_factory=list)
    # For FR strata. Values are int | None because CorpusOutcome's
    # count fields are int | None (see lazy-eval fields above); in
    # practice FR strata always populate them with concrete ints.
    per_stratum: dict[str, dict[str, int | None]] = field(default_factory=dict)
    # Which evaluation mode this corpus used. Lets consumers of the
    # manifest tell "we saw the whole population" (eager) apart from
    # "we stopped after materializing N eligible" (lazy).
    eligibility_evaluation: str = "eager_full_materialization"
    # PMC-OA style lazy-walk annotations. None for eager corpora.
    n_metadata_records_probed: int | None = None
    n_eligible_dev_materialized_before_stop: int | None = None
    # None means NOT MATERIALIZED — do not misread as "population is 0".
    n_eligible_dev_population_size: int | None = None
    n_audit_neighbors: int | None = None


def _rank(canonical_id: str) -> str:
    return hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()


def rank_within_dev(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Filter to Partition.DEV and sort by SHA-256(canonical_id) ascending."""
    dev = [
        c
        for c in candidates
        if assign_partition(c.canonical_id) is Partition.DEV
    ]
    dev.sort(key=lambda c: c.partition_hash_hex)
    return dev


def load_exclusion_ledger(path: Path, corpus: str) -> list[ExclusionEntry]:
    """Load an append-only JSONL exclusion ledger.

    Ledger schemas across corpora differ slightly; we extract the
    canonical-ID field appropriate to ``corpus``. Missing ledger file
    is treated as "no known exclusions" (fresh corpus); the caller
    should verify this is expected.
    """
    entries: list[ExclusionEntry] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cid: str | None
        if corpus == "pmc_oa":
            pmcid = row.get("pmcid")
            v = row.get("version")
            cid = f"{pmcid}.{v}" if pmcid and v else pmcid
        elif corpus == "doclaynet":
            cid = row.get("page_hash")
        elif corpus == "federal_register":
            cid = row.get("document_number")
        else:
            cid = None
        if not cid:
            continue
        entries.append(
            ExclusionEntry(
                canonical_id=cid,
                corpus=corpus,
                reason=row.get("reason", "unspecified"),
                source=str(path),
            )
        )
    return entries


def select_from_dev(
    candidates: Iterable[Candidate],
    excluded_ids: set[str],
    target_n: int,
    corpus: str,
    *,
    stratum_key: str | None = None,
) -> tuple[list[Candidate], CorpusOutcome]:
    """Run the locked selection pipeline over one already-eligible candidate list.

    Returns (selected, outcome). ``outcome`` records neighborhood
    diagnostics so a reviewer can see which candidates fell just
    outside the cutoff.
    """
    outcome = CorpusOutcome(corpus=corpus)
    candidates = list(candidates)
    outcome.n_eligible = len(candidates)

    kept = [c for c in candidates if c.canonical_id not in excluded_ids]
    outcome.n_after_exclusion = len(kept)
    outcome.excluded_by_ledger = [
        {"canonical_id": c.canonical_id, "metadata": c.metadata}
        for c in candidates
        if c.canonical_id in excluded_ids
    ]

    dev = rank_within_dev(kept)
    outcome.n_in_dev = len(dev)

    if len(dev) < target_n:
        raise InsufficientEligibleError(
            f"{corpus}"
            + (f" stratum={stratum_key!r}" if stratum_key else "")
            + f": only {len(dev)} eligible DEV candidates but target_n={target_n}. "
            f"Do NOT relax eligibility, change the hash partition, or reach into "
            f"CAL/held-out. STOP and surface the shortfall to the human."
        )

    selected = dev[:target_n]
    outcome.n_selected = len(selected)
    outcome.selected = [
        {
            "canonical_id": c.canonical_id,
            "partition_hash_hex": c.partition_hash_hex,
            "selection_rank": i,
            "metadata": c.metadata,
        }
        for i, c in enumerate(selected)
    ]

    # Show up to 5 cutoff-neighbor candidates so the reviewer can
    # verify we didn't swap #N for #N+1.
    lo = max(0, target_n - 2)
    hi = min(len(dev), target_n + 5)
    neighbors: list[dict[str, Any]] = []
    for i in range(lo, hi):
        neighbors.append(
            {
                "canonical_id": dev[i].canonical_id,
                "partition_hash_hex": dev[i].partition_hash_hex,
                "rank": i,
                "selected": i < target_n,
            }
        )
    outcome.dev_rank_neighbors = neighbors
    return selected, outcome


def select_stratified(
    candidates_by_stratum: dict[str, list[Candidate]],
    excluded_ids: set[str],
    corpus: str,
    quotas: dict[str, int],
) -> tuple[list[Candidate], CorpusOutcome]:
    """Federal Register stratified selection: run select_from_dev per stratum."""
    outcome = CorpusOutcome(corpus=corpus)
    outcome.n_enumerated = sum(len(v) for v in candidates_by_stratum.values())
    outcome.n_eligible = outcome.n_enumerated
    all_selected: list[Candidate] = []
    for stratum, target in quotas.items():
        cand = candidates_by_stratum.get(stratum, [])
        sub_sel, sub_outcome = select_from_dev(
            cand, excluded_ids, target, corpus, stratum_key=stratum
        )
        outcome.per_stratum[stratum] = {
            "n_eligible": sub_outcome.n_eligible,
            "n_after_exclusion": sub_outcome.n_after_exclusion,
            "n_in_dev": sub_outcome.n_in_dev,
            "n_selected": sub_outcome.n_selected,
            "target_n": target,
        }
        outcome.excluded_by_ledger.extend(sub_outcome.excluded_by_ledger)
        outcome.dev_rank_neighbors.extend(
            [{**row, "stratum": stratum} for row in sub_outcome.dev_rank_neighbors]
        )
        outcome.selected.extend(
            [{**row, "stratum": stratum} for row in sub_sel_row(sub_sel, stratum)]
        )
        all_selected.extend(sub_sel)
    outcome.n_after_exclusion = sum(
        (s["n_after_exclusion"] or 0) for s in outcome.per_stratum.values()
    )
    outcome.n_in_dev = sum(
        (s["n_in_dev"] or 0) for s in outcome.per_stratum.values()
    )
    outcome.n_selected = len(all_selected)
    return all_selected, outcome


def sub_sel_row(sub_sel: list[Candidate], stratum: str) -> list[dict[str, Any]]:
    return [
        {
            "canonical_id": c.canonical_id,
            "partition_hash_hex": c.partition_hash_hex,
            "selection_rank": i,
            "metadata": c.metadata,
        }
        for i, c in enumerate(sub_sel)
    ]


# --- Invariants -----------------------------------------------------


def verify_pilot_invariants(
    selected_pmc: list[Candidate],
    selected_doclaynet: list[Candidate],
    selected_fr: list[Candidate],
    exclusion_ids: set[str],
    fr_outcome: CorpusOutcome,
) -> None:
    """Refuse to emit the manifest unless every invariant holds."""
    if len(selected_pmc) != PMC_OA_TARGET_N:
        raise SelectionError(
            f"invariant violated: PMC == {PMC_OA_TARGET_N}, "
            f"got {len(selected_pmc)}"
        )
    if len(selected_doclaynet) != DOCLAYNET_TARGET_N:
        raise SelectionError(
            f"invariant violated: DocLayNet == {DOCLAYNET_TARGET_N}, "
            f"got {len(selected_doclaynet)}"
        )
    if len(selected_fr) != FEDERAL_REGISTER_TARGET_N:
        raise SelectionError(
            f"invariant violated: Federal Register == {FEDERAL_REGISTER_TARGET_N}, "
            f"got {len(selected_fr)}"
        )
    total = len(selected_pmc) + len(selected_doclaynet) + len(selected_fr)
    if total != 20:
        raise SelectionError(f"invariant violated: N == 20, got {total}")

    # FR stratum quotas
    for stratum, target in FR_STRATA.items():
        row = fr_outcome.per_stratum.get(stratum, {})
        if row.get("n_selected") != target:
            raise SelectionError(
                f"invariant violated: FR stratum {stratum!r} == {target}, "
                f"got {row.get('n_selected')}"
            )

    # All selected must be DEV
    for c in selected_pmc + selected_doclaynet + selected_fr:
        if assign_partition(c.canonical_id) is not Partition.DEV:
            raise SelectionError(
                f"invariant violated: canonical_id {c.canonical_id!r} not in DEV"
            )

    # No canonical_id in exclusion ledgers
    for c in selected_pmc + selected_doclaynet + selected_fr:
        if c.canonical_id in exclusion_ids:
            raise SelectionError(
                f"invariant violated: canonical_id {c.canonical_id!r} appears "
                f"in an exclusion ledger"
            )

    # No duplicates across the full 20
    all_ids = [c.canonical_id for c in selected_pmc + selected_doclaynet + selected_fr]
    if len(set(all_ids)) != len(all_ids):
        raise SelectionError("invariant violated: duplicate canonical IDs in selection")


# --- Manifest emission ---------------------------------------------


def emit_dry_manifest(
    manifest_path: Path,
    population_snapshots: dict[str, Any],
    corpus_outcomes: dict[str, CorpusOutcome],
    exclusion_summary: dict[str, Any],
) -> None:
    """Write the DEV_PILOT_MANIFEST_V1.json.

    Called ONLY after invariants pass.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "study": "B1",
        "phase": "B1a-5a-dry",
        "allocation": {"pmc_oa": PMC_OA_TARGET_N, "doclaynet": DOCLAYNET_TARGET_N, "federal_register": FEDERAL_REGISTER_TARGET_N, "total": 20},
        "selection_algorithm_version": SELECTION_ALGORITHM_VERSION,
        "emitted_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "population_snapshots": population_snapshots,
        "exclusion_summary": exclusion_summary,
        "selected": _build_selected_view(corpus_outcomes),
        "corpus_outcomes": {
            k: {
                "corpus": v.corpus,
                "eligibility_evaluation": v.eligibility_evaluation,
                "n_enumerated": v.n_enumerated,
                "n_eligible": v.n_eligible,
                "n_after_exclusion": v.n_after_exclusion,
                "n_in_dev": v.n_in_dev,
                "n_selected": v.n_selected,
                "n_metadata_records_probed": v.n_metadata_records_probed,
                "n_eligible_dev_materialized_before_stop": (
                    v.n_eligible_dev_materialized_before_stop
                ),
                "n_eligible_dev_population_size": v.n_eligible_dev_population_size,
                "n_audit_neighbors": v.n_audit_neighbors,
                "per_stratum": v.per_stratum,
            }
            for k, v in corpus_outcomes.items()
        },
        "excluded": _build_excluded_view(corpus_outcomes),
        "cutoff_neighbors": {
            k: v.dev_rank_neighbors for k, v in corpus_outcomes.items()
        },
        "authorization": "B1a-5a",
        "hard_rule": (
            "These 20 documents become permanently development/calibration "
            "material and can never migrate into D. Any change to eligibility, "
            "exclusion, partition, or ranking that could alter selected IDs "
            "REQUIRES bumping selection_algorithm_version."
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def _build_selected_view(
    corpus_outcomes: dict[str, CorpusOutcome],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for corpus, outcome in corpus_outcomes.items():
        for row in outcome.selected:
            out.append({"corpus": corpus, **row})
    return out


def _build_excluded_view(
    corpus_outcomes: dict[str, CorpusOutcome],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for corpus, outcome in corpus_outcomes.items():
        for row in outcome.excluded_by_ledger:
            out.append({"corpus": corpus, **row})
    return out


# --- Report emission -----------------------------------------------


def emit_dry_report(
    report_path: Path,
    population_snapshots: dict[str, Any],
    corpus_outcomes: dict[str, CorpusOutcome],
    exclusion_summary: dict[str, Any],
    manifest_path: Path,
) -> None:
    """Human-readable Markdown companion to the JSON manifest."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# B1 Pilot Dry-Selection Report (B1a-5a)")
    lines.append("")
    lines.append(
        f"**Emitted:** {datetime.now(tz=UTC).isoformat(timespec='seconds')}"
    )
    lines.append(f"**Selection algorithm version:** `{SELECTION_ALGORITHM_VERSION}`")
    lines.append(f"**Manifest:** `{manifest_path}`")
    lines.append("")
    lines.append("## Population snapshots (frozen inputs, written before selection)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(population_snapshots, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("## Exclusion summary")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(exclusion_summary, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("## Per-corpus counts (eager-materialization corpora)")
    lines.append("")
    lines.append(
        "The columns below have honest population semantics only for corpora "
        "whose eligibility was evaluated eagerly (the full population was "
        "materialized and filtered). Lazy-walk corpora are reported separately "
        "immediately after this table."
    )
    lines.append("")
    lines.append("| Corpus | Enumerated | Eligible | After exclusion | In DEV | Selected |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    eager = [
        (c, o) for c, o in corpus_outcomes.items()
        if o.eligibility_evaluation == "eager_full_materialization"
    ]
    for corpus, outcome in eager:
        lines.append(
            f"| {corpus} | {outcome.n_enumerated} | {outcome.n_eligible} | "
            f"{outcome.n_after_exclusion} | {outcome.n_in_dev} | {outcome.n_selected} |"
        )
    lines.append("")
    lazy = [
        (c, o) for c, o in corpus_outcomes.items()
        if o.eligibility_evaluation != "eager_full_materialization"
    ]
    if lazy:
        lines.append("### Per-corpus counts (lazy-evaluation corpora)")
        lines.append("")
        lines.append(
            "For lazy-walk corpora, the full eligible-DEV population size is "
            "NOT MATERIALIZED. Selection stops after materializing enough "
            "eligible DEV candidates to cover the target N plus audit "
            "neighbors."
        )
        lines.append("")
        for corpus, outcome in lazy:
            lines.append(f"#### {corpus}")
            lines.append("")
            lines.append(f"- eligibility evaluation: `{outcome.eligibility_evaluation}`")
            lines.append(
                f"- frozen population (enumerated): `{outcome.n_enumerated}`"
            )
            lines.append(
                f"- metadata records probed: `{outcome.n_metadata_records_probed}`"
            )
            lines.append(
                "- eligible DEV records materialized before stop: "
                f"`{outcome.n_eligible_dev_materialized_before_stop}`"
            )
            lines.append(f"- selected: `{outcome.n_selected}`")
            lines.append(
                f"- audit neighbors: `{outcome.n_audit_neighbors}`"
            )
            pop = outcome.n_eligible_dev_population_size
            pop_label = "**NOT MATERIALIZED**" if pop is None else f"`{pop}`"
            lines.append(f"- full eligible DEV population size: {pop_label}")
            lines.append("")
    # FR per-stratum
    fr_outcome = corpus_outcomes.get("federal_register")
    if fr_outcome and fr_outcome.per_stratum:
        lines.append("### Federal Register per-stratum breakdown")
        lines.append("")
        lines.append("| Stratum | Eligible | After exclusion | In DEV | Selected | Target |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for stratum, row in fr_outcome.per_stratum.items():
            lines.append(
                f"| {stratum} | {row['n_eligible']} | {row['n_after_exclusion']} | "
                f"{row['n_in_dev']} | {row['n_selected']} | {row['target_n']} |"
            )
        lines.append("")
    lines.append("## Selected 20")
    lines.append("")
    lines.append("| Corpus | Rank | Canonical ID | Partition hash |")
    lines.append("|---|---:|---|---|")
    for corpus, outcome in corpus_outcomes.items():
        for row in outcome.selected:
            stratum_note = f" ({row.get('stratum', '')})" if row.get("stratum") else ""
            phash = str(row["partition_hash_hex"])
            lines.append(
                f"| {corpus}{stratum_note} | {row['selection_rank']} | "
                f"`{row['canonical_id']}` | `{phash[:16]}...` |"
            )
    lines.append("")
    lines.append("## Cutoff-neighbor audit")
    lines.append("")
    lines.append(
        "For each corpus (and each FR stratum), the candidates immediately "
        "around the selection cutoff. Rows with `selected=True` were taken; "
        "rows with `selected=False` sit just outside the cutoff. This proves "
        "no manual swap occurred."
    )
    lines.append("")
    lines.append("```json")
    lines.append(
        json.dumps(
            {k: v.dev_rank_neighbors for k, v in corpus_outcomes.items()},
            indent=2,
            sort_keys=True,
        )
    )
    lines.append("```")
    lines.append("")
    report_path.write_text("\n".join(lines))


# --- Helpers exposed for corpus adapters ---------------------------


def make_candidate(canonical_id: str, metadata: dict[str, Any], corpus: str) -> Candidate:
    return Candidate(
        canonical_id=canonical_id,
        partition_hash_hex=_rank(canonical_id),
        metadata=metadata,
        corpus=corpus,
    )


__all__ = [
    "DOCLAYNET_TARGET_N",
    "FEDERAL_REGISTER_TARGET_N",
    "FR_STRATA",
    "FR_TOTAL_TARGET",
    "PMC_OA_TARGET_N",
    "SELECTION_ALGORITHM_VERSION",
    "Candidate",
    "CorpusOutcome",
    "EligibilityDecision",
    "ExclusionEntry",
    "InsufficientEligibleError",
    "SelectionError",
    "emit_dry_manifest",
    "emit_dry_report",
    "load_exclusion_ledger",
    "make_candidate",
    "rank_within_dev",
    "select_from_dev",
    "select_stratified",
    "sub_sel_row",
    "verify_pilot_invariants",
]
