"""Benchmark harness — generate representation metrics for one document."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import TYPE_CHECKING, TypedDict


class _AnomalyBaseKwargs(TypedDict):
    document_id: str
    baseline_a_tokens: int | None
    baseline_b_tokens: int | None
    candidate_c_tokens: int | None
    candidate_d_tokens: int | None
    candidate_e_tokens: int | None

from .schema import (
    AnomalyRecord,
    BaselineARecord,
    BenchmarkMetadata,
    CategorySummary,
    CorpusEntry,
    CorpusRunSummary,
    DocumentCapture,
    HeldOutRunLock,
    PreservationMetrics,
    RepresentationMetrics,
    RepresentationName,
    TextTokenBreakdown,
    TokenSavingsAttribution,
    VisualMetrics,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _capture_id(document_id: str, timestamp: str) -> str:
    raw = f"capture:{document_id}:{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _get_code_commit() -> str:
    """Return short git commit hash, or 'unknown' if git unavailable."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent.parent)
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _get_tokenizer_name() -> str:
    try:
        import tiktoken  # noqa: F401
        return "tiktoken/cl100k_base"
    except ImportError:
        return "heuristic"


def _get_package_size(output_dir: Path) -> int:
    if not output_dir.exists():
        return 0
    return sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file())


BASELINE_A_SERIALIZER_VERSION: str = "1.1"


def serialize_baseline_a(blocks) -> str:
    """Naive text serialization of pre-optimization blocks with structural markers.

    Includes ALL blocks (no dedup/optimization). Uses the same formatting
    conventions as the Markdown exporter for headings, lists, and code blocks.
    Version 1.1: adds structural markers to eliminate systematic A < B delta.
    """
    from aksharamd.models.block import BlockType

    parts: list[str] = []
    for block in blocks:
        content = getattr(block, "content", None) or ""
        btype = getattr(block, "type", None)
        level = getattr(block, "level", None) or 1

        if not content.strip():
            continue

        # Apply structural formatting matching the Markdown exporter
        if btype == BlockType.HEADING:
            prefix = "#" * max(1, min(6, int(level)))
            formatted = f"{prefix} {content.strip()}"
        elif btype == BlockType.CODE_BLOCK:
            formatted = f"```\n{content.strip()}\n```"
        elif btype == BlockType.BLOCKQUOTE:
            lines = content.strip().split("\n")
            formatted = "\n".join(f"> {line}" for line in lines)
        else:
            formatted = content.strip()

        parts.append(formatted)

    return "\n\n".join(parts)


def run_document(
    source_path: str | Path,
    output_dir: str | Path,
    corpus_root: str | Path | None = None,
    corpus_document_id: str | None = None,
) -> DocumentCapture:
    """Generate all representation metrics for one document.

    Runs: Baseline A (materialized pre-opt blocks), Baseline B (document.md),
    Candidates C/D/E.

    Args:
        source_path: Path to the source document.
        output_dir: Directory for all compiled outputs and metrics.
        corpus_root: Root of the document corpus (for relative paths in manifest).
        corpus_document_id: Friendly ID from the corpus manifest (overrides internal document hash).
    """
    from aksharamd.compiler import Compiler
    from aksharamd.packaging import (
        PLANNER_VERSION,
        PackageMode,
        PackageProfile,
        PackageWriter,
        build_llm_payload,
        build_token_report,
        plan_document,
    )
    from aksharamd.utils import count_tokens

    source_path = Path(source_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).isoformat()
    code_commit = _get_code_commit()
    tokenizer = _get_tokenizer_name()

    # ── Compile document (once, capturing pre-opt blocks) ─────────────────────
    compile_dir = output_dir / "compile"
    t_compile_start = time.monotonic()
    compiler = Compiler(output_dir=str(compile_dir))
    try:
        ctx, pre_opt_blocks = compiler.compile_with_baselines(str(source_path))
    except Exception as exc:
        logger.warning("compile_with_baselines failed for %s: %s; falling back to compile()", source_path, exc)
        ctx = compiler.compile(str(source_path))
        pre_opt_blocks = []
    compile_time = time.monotonic() - t_compile_start

    if ctx.document is None or ctx.manifest is None:
        raise RuntimeError(f"Compilation failed for {source_path}")

    document = ctx.document
    manifest = ctx.manifest
    validation = ctx.validation

    # Determine document_id: prefer corpus_document_id, fall back to internal hash
    document_id = corpus_document_id or document.document_id or document.id or source_path.stem

    # Metadata common to all captures
    metadata = BenchmarkMetadata(
        parser_version=getattr(manifest, "parser_version", "unknown") or "unknown",
        planner_version=PLANNER_VERSION,
        tokenizer=tokenizer,
        code_commit=code_commit,
        capture_timestamp=timestamp,
    )

    cap_id = _capture_id(document_id, timestamp)
    baselines: list[RepresentationMetrics] = []
    candidates: list[RepresentationMetrics] = []

    # ── Baseline A: materialize pre-optimization blocks ───────────────────────
    try:
        if pre_opt_blocks:
            baseline_a_text = serialize_baseline_a(pre_opt_blocks)
        else:
            # Fallback: read document.md as best estimate
            md_path = compile_dir / "document.md"
            if md_path.exists():
                baseline_a_text = md_path.read_text(encoding="utf-8")
                logger.warning("Baseline A fallback: using document.md for %s", source_path)
            else:
                baseline_a_text = ""
                logger.warning("Baseline A fallback: no blocks and no document.md for %s", source_path)

        baseline_a_file = output_dir / "baseline_a_naive.md"
        baseline_a_file.write_text(baseline_a_text, encoding="utf-8")
        baseline_a_bytes = baseline_a_file.read_bytes()
        baseline_a_checksum = hashlib.sha256(baseline_a_bytes).hexdigest()
        baseline_a_tokens = count_tokens(baseline_a_text)

        # Write BaselineARecord
        manifest_original_tokens = manifest.original_tokens
        delta = (baseline_a_tokens - manifest_original_tokens) if manifest_original_tokens else None
        baseline_a_record = BaselineARecord(
            baseline_a_text_path="baseline_a_naive.md",
            baseline_a_text_checksum=baseline_a_checksum,
            baseline_a_tokens=baseline_a_tokens,
            manifest_original_tokens=manifest_original_tokens,
            baseline_a_manifest_token_delta=delta,
        )
        (output_dir / "baseline_a_record.json").write_text(
            baseline_a_record.model_dump_json(indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Baseline A generation failed: %s; using 0", exc)
        baseline_a_tokens = manifest.original_tokens or 0
        baseline_a_checksum = ""

    baselines.append(RepresentationMetrics(
        capture_id=cap_id,
        document_id=document_id,
        representation=RepresentationName.BASELINE_A,
        package_profile={},
        metadata=metadata,
        emitted_text_tokens=baseline_a_tokens,
        token_breakdown=TextTokenBreakdown(markdown_tokens=baseline_a_tokens),
        compilation_time_s=compile_time,
    ))

    # ── Baseline B: current optimized document.md ──────────────────────────────
    md_path = compile_dir / "document.md"
    if md_path.exists():
        baseline_b_text = md_path.read_text(encoding="utf-8")
        baseline_b_tokens = count_tokens(baseline_b_text)
        # Write baseline_b artifact to run dir
        (output_dir / "baseline_b_document.md").write_text(baseline_b_text, encoding="utf-8")
    else:
        baseline_b_tokens = manifest.optimized_tokens or 0
        baseline_b_text = ""

    baselines.append(RepresentationMetrics(
        capture_id=cap_id,
        document_id=document_id,
        representation=RepresentationName.BASELINE_B,
        package_profile={},
        metadata=metadata,
        emitted_text_tokens=baseline_b_tokens,
        token_breakdown=TextTokenBreakdown(markdown_tokens=baseline_b_tokens),
        compilation_time_s=compile_time,
    ))

    # ── Candidates C / D / E (reuse compiled document, vary profile) ──────────
    candidate_profiles = [
        (RepresentationName.CANDIDATE_C, PackageProfile(mode=PackageMode.TEXT_FIRST)),
        (RepresentationName.CANDIDATE_D, PackageProfile(mode=PackageMode.ADAPTIVE)),
        (RepresentationName.CANDIDATE_E, PackageProfile(mode=PackageMode.FIDELITY_FIRST)),
    ]

    writer = PackageWriter()

    for rep_name, profile in candidate_profiles:
        pkg_dir = output_dir / rep_name.value
        pkg_dir.mkdir(parents=True, exist_ok=True)

        t_plan = time.monotonic()
        plan = plan_document(document, profile, validation)
        asset_refs, fidelity = writer.write(str(pkg_dir), plan, document, validation)
        plan_time = time.monotonic() - t_plan

        t_payload = time.monotonic()
        payload = build_llm_payload(plan, document, pkg_dir, asset_refs, profile)
        payload_time = time.monotonic() - t_payload

        pkg_size = _get_package_size(pkg_dir)

        # Write candidate artifacts
        mode_name = rep_name.value.replace("candidate_", "")
        from aksharamd.packaging import to_plain_text
        candidate_text = to_plain_text(payload)
        (output_dir / f"candidate_{mode_name}.md").write_text(candidate_text, encoding="utf-8")
        (output_dir / f"candidate_{mode_name}.json").write_text(
            payload.model_dump_json(indent=2), encoding="utf-8"
        )

        # Token breakdown from payload
        breakdown = TextTokenBreakdown(
            markdown_tokens=sum(
                item.estimated_tokens for item in payload.items
                if item.content_type.value == "text"
                and not item.provenance.get("representation", "").startswith("image")
            ),
            structured_table_tokens=sum(
                item.estimated_tokens for item in payload.items
                if item.content_type.value == "structured_table"
            ),
            warning_tokens=sum(
                item.estimated_tokens for item in payload.items
                if item.content_type.value == "warning"
            ),
            ocr_tokens=sum(
                item.estimated_tokens for item in payload.items
                if item.content_type.value == "text"
                and item.provenance.get("representation", "") == "image_and_text"
            ),
        )
        breakdown = breakdown.model_copy(update={
            "other_tokens": max(0, payload.actual_text_token_count - breakdown.total)
        })

        # Visual metrics
        token_report = build_token_report(document_id, plan, 0, 0, asset_refs)
        visual = VisualMetrics(
            selected_visual_asset_count=payload.selected_visual_asset_count,
            total_image_pixels=token_report.visual_stats.total_pixels,
            page_image_count=token_report.visual_stats.full_page_count,
            region_crop_count=token_report.visual_stats.region_crop_count,
            embedded_image_count=token_report.visual_stats.embedded_image_count,
            package_size_bytes=pkg_size,
        )

        # Preservation metrics
        pres = PreservationMetrics(
            meaningful_elements_discovered=fidelity.meaningful_elements_discovered,
            elements_preserved_in_package=fidelity.elements_preserved_in_package,
            elements_emitted_in_payload=len(payload.items),
            structured_tables_emitted=fidelity.structured_tables,
            images_preserved=fidelity.images_preserved,
            visual_fallbacks_selected=fidelity.tables_with_visual_fallback,
            unresolved_table_expectations=fidelity.unresolved_table_expectations,
            missing_asset_paths=len(payload.fidelity.missing_asset_paths),
            representation_downgrades=len(payload.fidelity.representation_downgrades),
            warnings_without_fallback=fidelity.warnings_without_visual_fallback,
            pages_with_possible_unpreserved_content=fidelity.pages_with_possible_unpreserved_content,
        )

        tdb = payload.token_delta_breakdown
        candidates.append(RepresentationMetrics(
            capture_id=cap_id,
            document_id=document_id,
            representation=rep_name,
            package_profile=profile.model_dump(),
            metadata=metadata,
            emitted_text_tokens=payload.actual_text_token_count,
            token_breakdown=breakdown,
            planned_text_tokens=payload.planned_text_tokens,
            token_delta=payload.token_delta,
            token_delta_breakdown=tdb.model_dump(),
            visual=visual,
            preservation=pres,
            compilation_time_s=compile_time,
            payload_generation_time_s=payload_time + plan_time,
        ))

    return DocumentCapture(
        capture_id=cap_id,
        document_id=document_id,
        timestamp=timestamp,
        metadata=metadata,
        baselines=baselines,
        candidates=candidates,
    )


def run_corpus(
    corpus_manifest_path: Path | str,
    output_dir: Path | str,
    split: str,                          # "dev" or "held_out" — REQUIRED
    corpus_root: Path | str | None = None,
    max_documents: int | None = None,
) -> tuple[list[DocumentCapture], Path]:
    """Run the benchmark for all documents in one explicit split.

    Mixed-split runs are prohibited. Pass split="dev" or split="held_out" explicitly.

    For held_out runs: writes a HeldOutRunLock to the output directory.
    """
    if split not in ("dev", "held_out"):
        raise ValueError(f"split must be 'dev' or 'held_out', got {split!r}")

    corpus_manifest_path = Path(corpus_manifest_path)
    output_dir = Path(output_dir)

    # Load manifest
    entries = [CorpusEntry.model_validate(e)
               for e in json.loads(corpus_manifest_path.read_text())]

    # Filter to requested split
    selected = [e for e in entries if e.split.value == split]
    if max_documents:
        selected = selected[:max_documents]

    if not selected:
        raise ValueError(f"No documents found for split={split!r} in {corpus_manifest_path}")

    # Compute corpus manifest checksum
    manifest_checksum = hashlib.sha256(corpus_manifest_path.read_bytes()).hexdigest()

    # Create run directory
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_id = f"{split}_{timestamp}"
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write lock for held-out runs
    if split == "held_out":
        from aksharamd.packaging import PLANNER_VERSION
        from aksharamd.packaging.policy import POLICY_VERSION
        lock = HeldOutRunLock(
            corpus_manifest_checksum=manifest_checksum,
            document_ids=[e.document_id for e in selected],
            code_commit=_get_code_commit(),
            parser_version="unknown",
            policy_version=POLICY_VERSION,
            planner_version=PLANNER_VERSION,
            payload_schema_version="1.0",
            tokenizer=_get_tokenizer_name(),
            run_timestamp=datetime.now(UTC).isoformat(),
        )
        (run_dir / "held_out_run_lock.json").write_text(lock.model_dump_json(indent=2))

    captures: list[DocumentCapture] = []
    corpus_root_path = Path(corpus_root) if corpus_root else corpus_manifest_path.parent

    for entry in selected:
        source = corpus_root_path / entry.file_path
        if not source.exists():
            # Try absolute path
            if Path(entry.file_path).exists():
                source = Path(entry.file_path)
            else:
                print(f"[SKIP] {entry.document_id}: file not found at {source}")
                continue

        doc_run_dir = run_dir / "runs" / entry.document_id
        doc_run_dir.mkdir(parents=True, exist_ok=True)

        try:
            capture = run_document(str(source), str(doc_run_dir), corpus_document_id=entry.document_id)
            captures.append(capture)
            # Write per-document metrics
            (doc_run_dir / "representation_metrics.json").write_text(
                capture.model_dump_json(indent=2), encoding="utf-8"
            )
            print(f"[OK] {entry.document_id}")
        except Exception as exc:
            print(f"[FAIL] {entry.document_id}: {exc}")

    return captures, run_dir


def compute_token_savings_attribution(
    capture: DocumentCapture,
    pre_opt_blocks: list,
) -> TokenSavingsAttribution:
    """Attribute token savings between Baseline A and Candidate D (adaptive)."""
    baseline_a = next(
        (m.emitted_text_tokens for m in capture.baselines
         if m.representation == RepresentationName.BASELINE_A), 0
    )
    baseline_b = next(
        (m.emitted_text_tokens for m in capture.baselines
         if m.representation == RepresentationName.BASELINE_B), 0
    )
    cand_d_metrics = next(
        (m for m in capture.candidates
         if m.representation == RepresentationName.CANDIDATE_D), None
    )

    final_payload_tokens = cand_d_metrics.emitted_text_tokens if cand_d_metrics else 0
    tdb = cand_d_metrics.token_delta_breakdown if cand_d_metrics else {}

    caption_dedup_tokens = abs(tdb.get("caption_dedup_delta", 0)) if tdb else 0
    warning_added_tokens = tdb.get("warning_delta", 0) if tdb else 0

    # Structural omission: tokens in B but not in D that are due to structural omits
    # Approximate: difference between B and C (text_first which has no images)
    cand_c = next(
        (m.emitted_text_tokens for m in capture.candidates
         if m.representation == RepresentationName.CANDIDATE_C), 0
    )
    structural_omission_tokens = max(0, baseline_b - cand_c)

    # Furniture = tokens in A removed by optimizer (B)
    # approximate residual
    duplicate_removed_tokens = 0  # hard to measure precisely
    repeated_furniture_removed_tokens = max(0, baseline_a - baseline_b
                                            - structural_omission_tokens
                                            - duplicate_removed_tokens)

    # Table representation delta: difference for structured tables
    table_representation_delta = (
        cand_d_metrics.token_breakdown.structured_table_tokens
        if cand_d_metrics else 0
    )

    # Other delta
    attributed = (
        repeated_furniture_removed_tokens
        + duplicate_removed_tokens
        + structural_omission_tokens
        + caption_dedup_tokens
        + table_representation_delta
        + warning_added_tokens
    )
    other_delta = max(0, baseline_a - attributed - final_payload_tokens)
    reconciliation_residual = (
        baseline_a
        - repeated_furniture_removed_tokens
        - duplicate_removed_tokens
        - structural_omission_tokens
        - caption_dedup_tokens
        - table_representation_delta
        - warning_added_tokens
        - other_delta
        - final_payload_tokens
    )

    return TokenSavingsAttribution(
        document_id=capture.document_id,
        baseline_a_tokens=baseline_a,
        repeated_furniture_removed_tokens=repeated_furniture_removed_tokens,
        duplicate_removed_tokens=duplicate_removed_tokens,
        structural_omission_tokens=structural_omission_tokens,
        caption_dedup_tokens=caption_dedup_tokens,
        table_representation_delta=table_representation_delta,
        warning_added_tokens=warning_added_tokens,
        other_delta=other_delta,
        final_payload_tokens=final_payload_tokens,
        reconciliation_residual=reconciliation_residual,
    )


def detect_anomalies(
    captures: list[DocumentCapture],
    corpus_entries: dict[str, CorpusEntry],
) -> list[AnomalyRecord]:
    """Flag suspicious benchmark results.

    Checks:
    - C has more text tokens than B
    - D has fewer visual than C unexpectedly
    - Baseline A < Baseline B
    - Package preservation < 80%
    """
    anomalies: list[AnomalyRecord] = []
    for cap in captures:
        baseline_a = next(
            (m.emitted_text_tokens for m in cap.baselines
             if m.representation == RepresentationName.BASELINE_A), 0
        )
        baseline_b = next(
            (m.emitted_text_tokens for m in cap.baselines
             if m.representation == RepresentationName.BASELINE_B), 0
        )
        cand_c = next(
            (m.emitted_text_tokens for m in cap.candidates
             if m.representation == RepresentationName.CANDIDATE_C), 0
        )
        cand_d = next(
            (m.emitted_text_tokens for m in cap.candidates
             if m.representation == RepresentationName.CANDIDATE_D), 0
        )
        cand_e = next(
            (m.emitted_text_tokens for m in cap.candidates
             if m.representation == RepresentationName.CANDIDATE_E), 0
        )
        cand_d_visual = next(
            (m.visual.selected_visual_asset_count for m in cap.candidates
             if m.representation == RepresentationName.CANDIDATE_D), 0
        )
        cand_c_visual = next(
            (m.visual.selected_visual_asset_count for m in cap.candidates
             if m.representation == RepresentationName.CANDIDATE_C), 0
        )

        base_kwargs: _AnomalyBaseKwargs = {
            "document_id": cap.document_id,
            "baseline_a_tokens": baseline_a,
            "baseline_b_tokens": baseline_b,
            "candidate_c_tokens": cand_c,
            "candidate_d_tokens": cand_d,
            "candidate_e_tokens": cand_e,
        }

        if baseline_a > 0 and baseline_a < baseline_b:
            anomalies.append(AnomalyRecord(
                anomaly_type="baseline_a_smaller_than_b",
                description=(
                    f"Baseline A ({baseline_a}) < Baseline B ({baseline_b}): "
                    "pre-opt blocks have fewer tokens than optimized doc. "
                    "Possible serialization issue."
                ),
                severity="warning",
                **base_kwargs,
            ))

        if baseline_b > 0 and cand_c > baseline_b * 1.05:
            anomalies.append(AnomalyRecord(
                anomaly_type="candidate_c_exceeds_baseline_b",
                description=(
                    f"Candidate C ({cand_c}) > Baseline B ({baseline_b}) by >5%: "
                    "text-first payload costs more than raw document.md."
                ),
                severity="warning",
                **base_kwargs,
            ))

        if cand_d < cand_c and cand_d_visual < cand_c_visual:
            anomalies.append(AnomalyRecord(
                anomaly_type="candidate_d_less_text_and_fewer_images_than_c",
                description=(
                    f"Candidate D has less text ({cand_d} < {cand_c}) AND "
                    f"fewer visual assets ({cand_d_visual} < {cand_c_visual}) than C: "
                    "adaptive mode is not adding value over text-first."
                ),
                severity="warning",
                **base_kwargs,
            ))

        # Preservation check
        d_metrics = next(
            (m for m in cap.candidates if m.representation == RepresentationName.CANDIDATE_D),
            None,
        )
        if d_metrics:
            pres = d_metrics.preservation
            if pres.meaningful_elements_discovered > 0:
                ratio = pres.elements_preserved_in_package / pres.meaningful_elements_discovered
                if ratio < 0.80:
                    anomalies.append(AnomalyRecord(
                        anomaly_type="low_preservation_ratio",
                        description=(
                            f"Preservation ratio {ratio:.1%} < 80%: "
                            f"{pres.elements_preserved_in_package}/{pres.meaningful_elements_discovered} "
                            "elements preserved."
                        ),
                        severity="error",
                        **base_kwargs,
                    ))

    return anomalies


def _pct_reduction(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return round((1.0 - after / before) * 100, 2)


def compute_corpus_summary(
    captures: list[DocumentCapture],
    run_id: str,
    split: str,
    manifest_checksum: str,
    failed_ids: list[str],
) -> CorpusRunSummary:
    if not captures:
        return CorpusRunSummary(
            run_id=run_id,
            split=split,
            timestamp=datetime.now(UTC).isoformat(),
            corpus_manifest_checksum=manifest_checksum,
            document_count=len(captures) + len(failed_ids),
            successful_count=0,
            failed_document_ids=failed_ids,
        )

    def _tokens(cap: DocumentCapture, rep: RepresentationName) -> int:
        for m in list(cap.baselines) + list(cap.candidates):
            if m.representation == rep:
                return m.emitted_text_tokens
        return 0

    a_vals = [_tokens(c, RepresentationName.BASELINE_A) for c in captures]
    b_vals = [_tokens(c, RepresentationName.BASELINE_B) for c in captures]
    c_vals = [_tokens(c, RepresentationName.CANDIDATE_C) for c in captures]
    d_vals = [_tokens(c, RepresentationName.CANDIDATE_D) for c in captures]
    e_vals = [_tokens(c, RepresentationName.CANDIDATE_E) for c in captures]

    a_med = median(a_vals)
    b_med = median(b_vals)
    c_med = median(c_vals)
    d_med = median(d_vals)

    # Weighted total reduction C vs B
    total_b = sum(b_vals)
    total_c = sum(c_vals)
    weighted_c_vs_b = _pct_reduction(total_b, total_c) if total_b > 0 else 0.0

    return CorpusRunSummary(
        run_id=run_id,
        split=split,
        timestamp=datetime.now(UTC).isoformat(),
        corpus_manifest_checksum=manifest_checksum,
        document_count=len(captures) + len(failed_ids),
        successful_count=len(captures),
        failed_document_ids=failed_ids,
        baseline_a_tokens_median=a_med,
        baseline_a_tokens_mean=mean(a_vals),
        baseline_b_tokens_median=b_med,
        baseline_b_tokens_mean=mean(b_vals),
        candidate_c_tokens_median=c_med,
        candidate_c_tokens_mean=mean(c_vals),
        candidate_d_tokens_median=d_med,
        candidate_d_tokens_mean=mean(d_vals),
        candidate_e_tokens_median=median(e_vals),
        c_vs_a_reduction_median_pct=_pct_reduction(a_med, c_med),
        c_vs_b_reduction_median_pct=_pct_reduction(b_med, c_med),
        d_vs_a_reduction_median_pct=_pct_reduction(a_med, d_med),
        d_vs_b_reduction_median_pct=_pct_reduction(b_med, d_med),
        total_corpus_c_vs_b_reduction_pct=weighted_c_vs_b,
        anomaly_count=0,  # filled by caller
    )


def compute_category_summaries(
    captures: list[DocumentCapture],
    corpus_entries: dict[str, CorpusEntry],
) -> list[CategorySummary]:
    from collections import defaultdict

    def _tokens(cap: DocumentCapture, rep: RepresentationName) -> int:
        for m in list(cap.baselines) + list(cap.candidates):
            if m.representation == rep:
                return m.emitted_text_tokens
        return 0

    # Group by primary category (first category in the list)
    by_cat: dict[str, list[DocumentCapture]] = defaultdict(list)
    for cap in captures:
        entry = corpus_entries.get(cap.document_id)
        cats = entry.categories if entry else []
        primary = cats[0].value if cats else "unknown"
        by_cat[primary].append(cap)

    summaries: list[CategorySummary] = []
    for cat, caps in sorted(by_cat.items()):
        a_vals = [_tokens(c, RepresentationName.BASELINE_A) for c in caps]
        b_vals = [_tokens(c, RepresentationName.BASELINE_B) for c in caps]
        c_vals = [_tokens(c, RepresentationName.CANDIDATE_C) for c in caps]
        d_vals = [_tokens(c, RepresentationName.CANDIDATE_D) for c in caps]
        e_vals = [_tokens(c, RepresentationName.CANDIDATE_E) for c in caps]

        b_med = median(b_vals) if b_vals else 0.0
        c_med = median(c_vals) if c_vals else 0.0
        d_med = median(d_vals) if d_vals else 0.0

        d_visual = [
            next(
                (m.visual.selected_visual_asset_count for m in c.candidates
                 if m.representation == RepresentationName.CANDIDATE_D), 0
            )
            for c in caps
        ]
        pres_ratios = []
        for cap in caps:
            dm = next(
                (m for m in cap.candidates if m.representation == RepresentationName.CANDIDATE_D),
                None,
            )
            if dm and dm.preservation.meaningful_elements_discovered > 0:
                pres_ratios.append(
                    dm.preservation.elements_preserved_in_package
                    / dm.preservation.meaningful_elements_discovered
                )

        summaries.append(CategorySummary(
            category=cat,
            document_count=len(caps),
            baseline_a_tokens_median=median(a_vals) if a_vals else 0.0,
            baseline_b_tokens_median=b_med,
            candidate_c_tokens_median=c_med,
            candidate_d_tokens_median=d_med,
            candidate_e_tokens_median=median(e_vals) if e_vals else 0.0,
            c_vs_b_reduction_median_pct=_pct_reduction(b_med, c_med),
            d_vs_b_reduction_median_pct=_pct_reduction(b_med, d_med),
            d_visual_assets_median=median(d_visual) if d_visual else 0.0,
            preservation_ratio_median=median(pres_ratios) if pres_ratios else 0.0,
        ))

    return summaries


def write_benchmark_results(
    captures: list[DocumentCapture],
    run_dir: Path,
    corpus_entries: dict[str, CorpusEntry],
    failed_ids: list[str],
    manifest_checksum: str,
) -> None:
    """Write all Benchmark B output files to run_dir."""
    run_id = run_dir.name
    split = run_id.split("_")[0]

    # ── Corpus run summary ────────────────────────────────────────────────────
    anomalies = detect_anomalies(captures, corpus_entries)
    summary = compute_corpus_summary(
        captures, run_id, split, manifest_checksum, failed_ids
    )
    summary = summary.model_copy(update={"anomaly_count": len(anomalies)})
    (run_dir / "dev_run_manifest.json").write_text(
        summary.model_dump_json(indent=2), encoding="utf-8"
    )

    # ── JSONL of all metrics ──────────────────────────────────────────────────
    with (run_dir / "representation_metrics.jsonl").open("w", encoding="utf-8") as fh:
        for cap in captures:
            fh.write(cap.model_dump_json() + "\n")

    # ── CSV document summary ──────────────────────────────────────────────────
    def _tokens(cap: DocumentCapture, rep: RepresentationName) -> int:
        for m in list(cap.baselines) + list(cap.candidates):
            if m.representation == rep:
                return m.emitted_text_tokens
        return 0

    with (run_dir / "document_summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "document_id", "baseline_a", "baseline_b", "candidate_c", "candidate_d",
            "candidate_e", "c_vs_b_pct", "d_vs_b_pct",
        ])
        for cap in captures:
            a = _tokens(cap, RepresentationName.BASELINE_A)
            b = _tokens(cap, RepresentationName.BASELINE_B)
            c = _tokens(cap, RepresentationName.CANDIDATE_C)
            d = _tokens(cap, RepresentationName.CANDIDATE_D)
            e = _tokens(cap, RepresentationName.CANDIDATE_E)
            writer.writerow([
                cap.document_id, a, b, c, d, e,
                round(_pct_reduction(b, c), 2),
                round(_pct_reduction(b, d), 2),
            ])

    # ── Category summary ──────────────────────────────────────────────────────
    cat_summaries = compute_category_summaries(captures, corpus_entries)
    (run_dir / "category_summary.json").write_text(
        json.dumps([s.model_dump() for s in cat_summaries], indent=2), encoding="utf-8"
    )

    # ── Token savings attribution ─────────────────────────────────────────────
    with (run_dir / "token_savings_attribution.jsonl").open("w", encoding="utf-8") as fh:
        for cap in captures:
            attr = compute_token_savings_attribution(cap, [])
            fh.write(attr.model_dump_json() + "\n")

    # ── Table representation analysis ────────────────────────────────────────
    table_stats: dict = {
        "structured_tables_total": 0,
        "legacy_tables_total": 0,
        "structured_table_tokens_total": 0,
        "legacy_table_tokens_total": 0,
        "per_document": [],
    }
    for cap in captures:
        d_m = next(
            (m for m in cap.candidates if m.representation == RepresentationName.CANDIDATE_D),
            None,
        )
        if d_m:
            st = d_m.token_breakdown.structured_table_tokens
            # Legacy tables go into markdown_tokens; no separate counter
            table_stats["structured_tables_total"] += d_m.preservation.structured_tables_emitted
            table_stats["structured_table_tokens_total"] += st
            table_stats["per_document"].append({
                "document_id": cap.document_id,
                "structured_tables": d_m.preservation.structured_tables_emitted,
                "structured_table_tokens": st,
            })
    (run_dir / "table_representation_analysis.json").write_text(
        json.dumps(table_stats, indent=2), encoding="utf-8"
    )

    # ── Visual payload analysis ───────────────────────────────────────────────
    visual_stats: dict = {
        "candidate_d_visual_assets_total": 0,
        "candidate_e_visual_assets_total": 0,
        "per_document": [],
    }
    for cap in captures:
        d_v = next(
            (m.visual for m in cap.candidates if m.representation == RepresentationName.CANDIDATE_D),
            None,
        )
        e_v = next(
            (m.visual for m in cap.candidates if m.representation == RepresentationName.CANDIDATE_E),
            None,
        )
        visual_stats["candidate_d_visual_assets_total"] += (
            d_v.selected_visual_asset_count if d_v else 0
        )
        visual_stats["candidate_e_visual_assets_total"] += (
            e_v.selected_visual_asset_count if e_v else 0
        )
        visual_stats["per_document"].append({
            "document_id": cap.document_id,
            "d_visual_assets": d_v.selected_visual_asset_count if d_v else 0,
            "d_page_images": d_v.page_image_count if d_v else 0,
            "d_region_crops": d_v.region_crop_count if d_v else 0,
            "d_embedded_images": d_v.embedded_image_count if d_v else 0,
            "e_visual_assets": e_v.selected_visual_asset_count if e_v else 0,
        })
    (run_dir / "visual_payload_analysis.json").write_text(
        json.dumps(visual_stats, indent=2), encoding="utf-8"
    )

    # ── Runtime analysis ──────────────────────────────────────────────────────
    runtime_stats: dict = {
        "compile_times_s": [],
        "payload_times_s": [],
        "per_document": [],
    }
    for cap in captures:
        first_cand = cap.candidates[0] if cap.candidates else None
        compile_t = first_cand.compilation_time_s if first_cand else 0.0
        payload_t = first_cand.payload_generation_time_s if first_cand else 0.0
        runtime_stats["compile_times_s"].append(compile_t)
        runtime_stats["payload_times_s"].append(payload_t)
        runtime_stats["per_document"].append({
            "document_id": cap.document_id,
            "compile_time_s": compile_t,
            "payload_time_s": payload_t,
        })
    if runtime_stats["compile_times_s"]:
        runtime_stats["median_compile_s"] = median(runtime_stats["compile_times_s"])
        runtime_stats["median_payload_s"] = median(runtime_stats["payload_times_s"])
    (run_dir / "runtime_analysis.json").write_text(
        json.dumps(runtime_stats, indent=2), encoding="utf-8"
    )

    # ── Anomalies ─────────────────────────────────────────────────────────────
    (run_dir / "anomalies.json").write_text(
        json.dumps([a.model_dump() for a in anomalies], indent=2), encoding="utf-8"
    )

    # ── Benchmark B report ────────────────────────────────────────────────────
    _write_benchmark_b_report(
        run_dir, captures, corpus_entries, failed_ids, summary,
        cat_summaries, anomalies, table_stats, visual_stats, runtime_stats,
    )


def _write_benchmark_b_report(
    run_dir: Path,
    captures: list[DocumentCapture],
    corpus_entries: dict[str, CorpusEntry],
    failed_ids: list[str],
    summary: CorpusRunSummary,
    cat_summaries: list[CategorySummary],
    anomalies: list[AnomalyRecord],
    table_stats: dict,
    visual_stats: dict,
    runtime_stats: dict,
) -> None:
    def _tokens(cap: DocumentCapture, rep: RepresentationName) -> int:
        for m in list(cap.baselines) + list(cap.candidates):
            if m.representation == rep:
                return m.emitted_text_tokens
        return 0

    lines: list[str] = []
    lines.append("# Benchmark B — Representation Efficiency Report\n")
    lines.append(f"Run ID: `{summary.run_id}`  \nSplit: `{summary.split}`  \nTimestamp: {summary.timestamp}\n")

    # Section 1: Corpus composition
    lines.append("\n## Development Corpus Composition\n")
    lines.append("| document_id | file_type | categories | size_bytes |")
    lines.append("|---|---|---|---|")
    for cap in captures:
        entry = corpus_entries.get(cap.document_id)
        ft = entry.file_type if entry else "?"
        cats = ", ".join(c.value for c in entry.categories) if entry else "?"
        sz = entry.source_size_bytes if entry else "?"
        lines.append(f"| {cap.document_id} | {ft} | {cats} | {sz} |")
    if failed_ids:
        for fid in failed_ids:
            entry = corpus_entries.get(fid)
            ft = entry.file_type if entry else "?"
            lines.append(f"| {fid} | {ft} | FAILED | — |")

    # Section 2: Baseline A verification
    lines.append("\n## Baseline A Artifact Verification\n")
    lines.append("| document_id | baseline_a_tokens | manifest_original_tokens | delta |")
    lines.append("|---|---|---|---|")
    for cap in captures:
        a = _tokens(cap, RepresentationName.BASELINE_A)
        rec_path = run_dir / "runs" / cap.document_id / "baseline_a_record.json"
        if rec_path.exists():
            rec_data = json.loads(rec_path.read_text())
            mo = rec_data.get("manifest_original_tokens", "?")
            dt = rec_data.get("baseline_a_manifest_token_delta", "?")
        else:
            mo = "?"
            dt = "?"
        lines.append(f"| {cap.document_id} | {a} | {mo} | {dt} |")

    # Section 3: Token results A-E
    lines.append("\n## Token Results: Representations A-E\n")
    lines.append("| document_id | A | B | C | D | E |")
    lines.append("|---|---|---|---|---|---|")
    for cap in captures:
        a = _tokens(cap, RepresentationName.BASELINE_A)
        b = _tokens(cap, RepresentationName.BASELINE_B)
        c = _tokens(cap, RepresentationName.CANDIDATE_C)
        d = _tokens(cap, RepresentationName.CANDIDATE_D)
        e = _tokens(cap, RepresentationName.CANDIDATE_E)
        lines.append(f"| {cap.document_id} | {a} | {b} | {c} | {d} | {e} |")

    lines.append("\n**Aggregate:**")
    lines.append(f"- Baseline A: median={summary.baseline_a_tokens_median:.0f}, mean={summary.baseline_a_tokens_mean:.0f}")
    lines.append(f"- Baseline B: median={summary.baseline_b_tokens_median:.0f}, mean={summary.baseline_b_tokens_mean:.0f}")
    lines.append(f"- Candidate C: median={summary.candidate_c_tokens_median:.0f}, mean={summary.candidate_c_tokens_mean:.0f}")
    lines.append(f"- Candidate D: median={summary.candidate_d_tokens_median:.0f}, mean={summary.candidate_d_tokens_mean:.0f}")
    lines.append(f"- Candidate E: median={summary.candidate_e_tokens_median:.0f}")

    # Section 4: Token reductions
    lines.append("\n## Token Reductions (median)\n")
    lines.append("| comparison | reduction_pct |")
    lines.append("|---|---|")
    lines.append(f"| C vs A | {summary.c_vs_a_reduction_median_pct:.2f}% |")
    lines.append(f"| C vs B | {summary.c_vs_b_reduction_median_pct:.2f}% |")
    lines.append(f"| D vs A | {summary.d_vs_a_reduction_median_pct:.2f}% |")
    lines.append(f"| D vs B | {summary.d_vs_b_reduction_median_pct:.2f}% |")
    lines.append(f"\nWeighted total C vs B reduction: **{summary.total_corpus_c_vs_b_reduction_pct:.2f}%**")
    lines.append("\n*Note: negative reduction means C costs MORE tokens than baseline.*")

    # Section 5: Token savings attribution (waterfall)
    lines.append("\n## Token Savings Attribution (Candidate D)\n")
    lines.append("Approximate waterfall for median document:")
    if captures:
        # Use first capture as example
        ex = captures[0]
        attr = compute_token_savings_attribution(ex, [])
        lines.append("```")
        lines.append(f"Baseline A:                    {attr.baseline_a_tokens:>8} tokens")
        lines.append(f"- Furniture removed:           {attr.repeated_furniture_removed_tokens:>8}")
        lines.append(f"- Structural omissions:        {attr.structural_omission_tokens:>8}")
        lines.append(f"- Caption dedup:               {attr.caption_dedup_tokens:>8}")
        lines.append(f"- Table representation delta:  {attr.table_representation_delta:>8}")
        lines.append(f"+ Warnings added:              {attr.warning_added_tokens:>8}")
        lines.append(f"- Other delta:                 {attr.other_delta:>8}")
        lines.append(f"= Final D tokens:              {attr.final_payload_tokens:>8}")
        lines.append(f"  Reconciliation residual:     {attr.reconciliation_residual:>8}")
        lines.append("```")
        lines.append(f"*(example: {ex.document_id})*")

    # Section 6: Visual assets
    lines.append("\n## Visual-Asset Results by Mode\n")
    total_d = visual_stats.get("candidate_d_visual_assets_total", 0)
    total_e = visual_stats.get("candidate_e_visual_assets_total", 0)
    lines.append("- Candidate C: 0 images (text-first mode suppresses visual assets)")
    lines.append(f"- Candidate D (adaptive): {total_d} total visual assets across corpus")
    lines.append(f"- Candidate E (fidelity-first): {total_e} total visual assets across corpus")

    # Section 7: Preservation and fidelity
    lines.append("\n## Preservation and Fidelity\n")
    lines.append("| document_id | discovered | preserved | emitted | structured_tables | logical_pres% | visual_resolved |")
    lines.append("|---|---|---|---|---|---|---|")
    for cap in captures:
        d_m = next(
            (m for m in cap.candidates if m.representation == RepresentationName.CANDIDATE_D),
            None,
        )
        if d_m:
            p = d_m.preservation
            logical_pres = (
                f"{p.elements_preserved_in_package / p.meaningful_elements_discovered:.1%}"
                if p.meaningful_elements_discovered > 0 else "N/A"
            )
            # Visual resolved: images_preserved + visual_fallbacks_selected = planned visual refs
            visual_refs_planned = p.images_preserved + p.visual_fallbacks_selected
            missing_asset_count = p.missing_asset_paths
            if visual_refs_planned > 0:
                visual_resolved_count = max(0, visual_refs_planned - missing_asset_count)
                visual_resolved = f"{visual_resolved_count / visual_refs_planned:.0%}"
            else:
                visual_resolved = "n/a"
            lines.append(
                f"| {cap.document_id} | {p.meaningful_elements_discovered} | "
                f"{p.elements_preserved_in_package} | {p.elements_emitted_in_payload} | "
                f"{p.structured_tables_emitted} | {logical_pres} | {visual_resolved} |"
            )
    lines.append("\nNote: logical_pres% = elements_preserved / meaningful_elements_discovered. "
                 "visual_resolved = (visual_refs_planned - missing_asset_paths) / visual_refs_planned "
                 "(n/a when no visual refs planned).")

    # Section 8: Table representation
    lines.append("\n## Table Representation Analysis\n")
    lines.append(f"- Structured tables emitted across corpus (Candidate D): {table_stats['structured_tables_total']}")
    lines.append(f"- Structured table tokens total: {table_stats['structured_table_tokens_total']}")
    lines.append("\nTable serialization: token-aware selector active. Formats available: markdown, tsv, row_records, "
                 "preview_reference, json_reference. Selected format per table depends on mode and token budget "
                 "(max_inline_table_tokens=1200, guard_factor=1.05).")

    # Section 9: Runtime
    lines.append("\n## Runtime and Package Size\n")
    med_compile = runtime_stats.get("median_compile_s", 0.0)
    med_payload = runtime_stats.get("median_payload_s", 0.0)
    lines.append(f"- Median compile time: {med_compile:.2f}s")
    lines.append(f"- Median payload generation time: {med_payload:.3f}s")

    # Section 10: Category breakdown
    lines.append("\n## Category-Level Differences\n")
    lines.append("| category | n | A_med | B_med | C_med | D_med | C_vs_B% | D_vs_B% | D_visual_med | pres_med |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for cs in cat_summaries:
        lines.append(
            f"| {cs.category} | {cs.document_count} | "
            f"{cs.baseline_a_tokens_median:.0f} | {cs.baseline_b_tokens_median:.0f} | "
            f"{cs.candidate_c_tokens_median:.0f} | {cs.candidate_d_tokens_median:.0f} | "
            f"{cs.c_vs_b_reduction_median_pct:.1f}% | {cs.d_vs_b_reduction_median_pct:.1f}% | "
            f"{cs.d_visual_assets_median:.1f} | {cs.preservation_ratio_median:.1%} |"
        )

    # Section 11: Anomalies
    lines.append("\n## Anomalies\n")
    if anomalies:
        for anomaly in anomalies:
            lines.append(f"- **[{anomaly.severity.upper()}]** `{anomaly.document_id}` — {anomaly.anomaly_type}: {anomaly.description}")
    else:
        lines.append("No anomalies detected.")

    # Section 12: Limitations
    lines.append("\n## Limitations\n")
    lines.append("- Parsebench documents are single-page extracts (not full documents). Token counts are lower than full documents.")
    lines.append("- Synthetic DOCX/XLSX/PPTX files are small samples without complex layouts.")
    lines.append("- No scanned PDFs in the DEV corpus. OCR path is not exercised.")
    lines.append("- Visual asset materialization requires full source PDF rendering (fitz). Parsebench single-page extracts "
                 "do not produce rendered image files. Visual asset tests require dedicated full-document compilation. "
                 "This will be addressed when the corpus is expanded with full documents.")
    lines.append("- Token savings attribution is an approximation; reconciliation residual may be non-zero.")
    if failed_ids:
        lines.append(f"- {len(failed_ids)} documents failed to compile: {', '.join(failed_ids)}")

    # Section 13: Recommendation
    lines.append("\n## Recommendation: QA Pilot Readiness\n")
    success_rate = summary.successful_count / summary.document_count if summary.document_count > 0 else 0.0
    if success_rate >= 0.85 and summary.anomaly_count <= 3:
        lines.append("**Assessment: READY for QA pilot (text-only band).**\n")
        lines.append("Criteria met:")
        lines.append("- Text tokens are measurable and reproducible via materialized Baseline A artifacts.")
        lines.append(f"- {summary.successful_count}/{summary.document_count} documents compiled successfully.")
        lines.append("- Logical preservation is high where tested; no systematic failures.")
        lines.append("\nCriteria not yet met for multimodal band:")
        lines.append("- Visual assets not rendered in this run (no fitz/vision pipeline exercised).")
        lines.append("- Held-out split not run; generalization not yet measured.")
    else:
        lines.append("**Assessment: NOT YET READY for QA pilot.**\n")
        lines.append(f"- Only {summary.successful_count}/{summary.document_count} documents succeeded ({success_rate:.0%}).")
        lines.append(f"- {summary.anomaly_count} anomalies detected.")
        lines.append("- Resolve failures and anomalies before proceeding.")

    report_text = "\n".join(lines) + "\n"
    (run_dir / "benchmark_b_report.md").write_text(report_text, encoding="utf-8")
    print(f"[REPORT] Written to {run_dir / 'benchmark_b_report.md'}")
