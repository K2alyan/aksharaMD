"""Run the KV detector against the dev corpus and compute metrics."""
from __future__ import annotations

from benchmarks.kv_eval.ground_truth import CorpusMetrics, DetectionOutcome, KeyValueGroundTruth


def evaluate_text_case(
    text: str,
    ground_truth: KeyValueGroundTruth,
    profile=None,
) -> DetectionOutcome:
    """Run inline detector on text, compare to ground truth.

    ``profile`` is a KeyValueDetectionProfile. When None, the legacy v1
    detector path runs (used for backwards-compatible baseline metrics).
    """
    from aksharamd.scoring.key_value_detection import detect_key_value_entries
    result = detect_key_value_entries(text, page=1, profile=profile)
    predicted = result.group is not None
    path = "heuristic_inline" if predicted else None
    return DetectionOutcome(
        case_id=ground_truth.case_id,
        predicted_is_kv=predicted,
        predicted_group_type=result.group.group_type if result.group else None,
        predicted_entry_count=len(result.group.entries) if result.group else 0,
        predicted_record_count=_count_records(result.group) if result.group else 0,
        detection_path_used=path,
    )


def evaluate_html_case(html: str, ground_truth: KeyValueGroundTruth) -> DetectionOutcome:
    """Compile HTML through the parser, look for KEY_VALUE_GROUP blocks."""
    import os
    import tempfile

    from aksharamd.compiler import Compiler
    from aksharamd.models.block import BlockType

    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(f"<html><body>{html}</body></html>")
        fname = f.name
    try:
        out = tempfile.mkdtemp()
        ctx = Compiler(output_dir=out).compile(fname)
        blocks = ctx.document.blocks if ctx.document else []
        kv_blocks = [b for b in blocks if b.type == BlockType.KEY_VALUE_GROUP]
        predicted = len(kv_blocks) > 0
        first = kv_blocks[0] if kv_blocks else None
        return DetectionOutcome(
            case_id=ground_truth.case_id,
            predicted_is_kv=predicted,
            predicted_group_type=first.key_value_group.group_type if first and first.key_value_group else None,
            predicted_entry_count=len(first.key_value_group.entries) if first and first.key_value_group else 0,
            predicted_record_count=_count_records(first.key_value_group) if first and first.key_value_group else 0,
            detection_path_used="native_html_dl" if predicted else None,
            source_block_ids=[first.id] if first else [],
        )
    finally:
        try:
            os.unlink(fname)
        except Exception:
            pass


def evaluate_xlsx_case(xlsx_path: str, ground_truth: KeyValueGroundTruth) -> DetectionOutcome:
    """Compile XLSX file, look for KEY_VALUE_GROUP blocks."""
    import tempfile

    from aksharamd.compiler import Compiler
    from aksharamd.models.block import BlockType

    out = tempfile.mkdtemp()
    ctx = Compiler(output_dir=out).compile(xlsx_path)
    blocks = ctx.document.blocks if ctx.document else []
    kv_blocks = [b for b in blocks if b.type == BlockType.KEY_VALUE_GROUP]
    predicted = len(kv_blocks) > 0
    first = kv_blocks[0] if kv_blocks else None
    return DetectionOutcome(
        case_id=ground_truth.case_id,
        predicted_is_kv=predicted,
        predicted_entry_count=len(first.key_value_group.entries) if first and first.key_value_group else 0,
        detection_path_used="native_xlsx_kv" if predicted else None,
    )


def compute_corpus_metrics(
    outcomes: list[DetectionOutcome],
    ground_truths: dict[str, KeyValueGroundTruth],
    path_name: str,
) -> CorpusMetrics:
    metrics = CorpusMetrics(path_name=path_name)
    for o in outcomes:
        gt = ground_truths[o.case_id]
        actual = gt.is_key_value_group
        predicted = o.predicted_is_kv
        if actual and predicted:
            metrics.tp += 1
        elif actual and not predicted:
            metrics.fn += 1
        elif not actual and predicted:
            metrics.fp += 1
        else:
            metrics.tn += 1
    return metrics.compute()


def _count_records(group) -> int:
    if group is None:
        return 0
    seen: set[str] = set()
    count = 1
    for e in group.entries:
        if e.key in seen:
            count += 1
            seen = {e.key}
        else:
            seen.add(e.key)
    return count


def evaluate_adjacent_case(
    blocks: list,
    ground_truth: KeyValueGroundTruth,
    profile=None,
) -> DetectionOutcome:
    """Run the adjacent-block promoter on a pre-built block list.

    Creates a real CompilationContext, runs detect_and_promote_key_value_groups(),
    checks if a KEY_VALUE_GROUP block was produced.
    """
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.models.document import Document
    from aksharamd.plugins.transformers.key_value_promoter import detect_and_promote_key_value_groups

    doc = Document(source="adjacent_test", blocks=blocks, metadata={})
    ctx = CompilationContext(source="adjacent_test", document=doc)
    if profile is not None:
        ctx.kv_profile = profile
    result_ctx = detect_and_promote_key_value_groups(ctx, profile=profile)

    result_blocks = result_ctx.document.blocks if result_ctx.document else []
    kv_blocks = [b for b in result_blocks if b.type == BlockType.KEY_VALUE_GROUP]
    predicted = len(kv_blocks) > 0
    first = kv_blocks[0] if kv_blocks else None

    return DetectionOutcome(
        case_id=ground_truth.case_id,
        predicted_is_kv=predicted,
        predicted_group_type=first.key_value_group.group_type if first and first.key_value_group else None,
        predicted_entry_count=len(first.key_value_group.entries) if first and first.key_value_group else 0,
        detection_path_used="heuristic_adjacent" if predicted else None,
    )


def simulate_adjacent_threshold(
    text_cases, ground_truths: dict[str, KeyValueGroundTruth],
    min_blocks_options: list[int],
) -> dict[int, CorpusMetrics]:
    """Simulate different adjacent-block minimum thresholds offline (text approx).

    This does NOT change production behavior — it re-runs the detector
    with different thresholds on the text corpus.
    """
    results = {}
    for min_b in min_blocks_options:
        outcomes = []
        for case in text_cases:
            outcome = _simulate_with_threshold(case, min_b)
            outcomes.append(outcome)
        m = compute_corpus_metrics(outcomes, ground_truths, f"adjacent_min_{min_b}")
        results[min_b] = m
    return results


def _simulate_with_threshold(case, min_blocks: int) -> DetectionOutcome:
    """Re-run promoter pass 1 (inline) on text — pass 2 (adjacent) not simulated
    because it requires multi-block context. We approximate by testing
    whether the combined text would pass detection with the given min."""
    from aksharamd.scoring.key_value_config import KeyValueDetectionProfile
    from aksharamd.scoring.key_value_detection import detect_key_value_entries
    profile = KeyValueDetectionProfile.experimental()
    result = detect_key_value_entries(case.text, page=1, profile=profile)
    if result.group is not None:
        return DetectionOutcome(
            case_id=case.ground_truth.case_id,
            predicted_is_kv=True,
            detection_path_used="heuristic_inline",
        )
    from aksharamd.scoring.key_value_detection import _try_parse_kv_line
    lines = [ln.strip() for ln in case.text.splitlines() if ln.strip()]
    pairs = sum(1 for ln in lines if _try_parse_kv_line(ln) is not None)
    predicted = pairs >= min_blocks // 2 and pairs >= 2
    return DetectionOutcome(
        case_id=case.ground_truth.case_id,
        predicted_is_kv=predicted,
        detection_path_used="heuristic_adjacent_simulated" if predicted else None,
    )


def simulate_adjacent_threshold_real(
    adjacent_cases,
    ground_truths: dict[str, KeyValueGroundTruth],
    min_blocks_options: list[int],
    profile=None,
) -> dict[int, CorpusMetrics]:
    """Simulate different adjacent-block minimum thresholds against REAL
    alternating-block cases.

    Rather than the text-approximate simulator, this walks real Block objects
    and applies the same run-collection + Strategy1/Strategy2 logic as the
    production promoter — parameterised by ``min_blocks``.
    """
    from aksharamd.plugins.transformers.key_value_promoter import (
        _collect_adjacent_run,
        _parse_alternating_blocks,
    )
    from aksharamd.scoring.key_value_config import KeyValueDetectionProfile
    from aksharamd.scoring.key_value_detection import detect_key_value_entries
    if profile is None:
        profile = KeyValueDetectionProfile.experimental()

    results: dict[int, CorpusMetrics] = {}
    for min_b in min_blocks_options:
        outcomes: list[DetectionOutcome] = []
        for case in adjacent_cases:
            outcome = _simulate_adjacent_case_at_threshold(
                case, min_b, profile,
                _collect_adjacent_run, _parse_alternating_blocks,
                detect_key_value_entries,
            )
            outcomes.append(outcome)
        m = compute_corpus_metrics(
            outcomes, ground_truths, f"adjacent_real_min_{min_b}"
        )
        results[min_b] = m
    return results


def _simulate_adjacent_case_at_threshold(
    case, min_blocks, profile,
    collect_adjacent_run, parse_alternating_blocks, detect_kv,
) -> DetectionOutcome:
    """Mimic _pass_adjacent with a parameterised min_blocks threshold."""
    blocks = case.blocks
    predicted = False
    i = 0
    while i < len(blocks):
        run, end_i = collect_adjacent_run(blocks, i)
        if run is not None and len(run) >= min_blocks:
            first_page = next(
                (b.page for b in run if b.page is not None), None
            )
            combined = "\n".join(
                (b.content or "").strip() for b in run
            )
            result = detect_kv(combined, page=first_page, profile=profile)
            if result.group is not None:
                predicted = True
                break
            alt = parse_alternating_blocks(run)
            if len(alt) >= 2:
                virtual = "\n".join(f"{c.key}: {c.value}" for c in alt)
                result2 = detect_kv(virtual, page=first_page, profile=profile)
                if result2.group is not None:
                    predicted = True
                    break
            i = end_i
        else:
            i += 1
    return DetectionOutcome(
        case_id=case.ground_truth.case_id,
        predicted_is_kv=predicted,
        detection_path_used="heuristic_adjacent" if predicted else None,
    )
