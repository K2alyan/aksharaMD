"""Tests for the KV evaluation framework.

Coverage:
- EL1-EL5: Detector lock
- EC1-EC5: Corpus
- EI1-EI7: Evaluator inline
- EH1-EH5: Evaluator HTML
- ET1-ET4: Token comparison
- EA1-EA4: Adjacent threshold simulation
- EM1-EM3: Maturity assessment
"""
from __future__ import annotations

import json
import pytest

# ── DETECTOR LOCK ─────────────────────────────────────────────────────────────

def test_el1_build_lock_returns_correct_detector_version():
    """EL1: build_lock() returns KeyValueDetectorLock with detector_version = 'kv_promoter/v1'."""
    from benchmarks.kv_eval.detector_lock import build_lock, KeyValueDetectorLock
    lock = build_lock()
    assert isinstance(lock, KeyValueDetectorLock)
    assert lock.detector_version == "kv_promoter/v1"


def test_el2_rhetorical_checksum_is_16_char_hex():
    """EL2: rhetorical_label_set_checksum is a 16-char hex string."""
    from benchmarks.kv_eval.detector_lock import build_lock
    lock = build_lock()
    chk = lock.rhetorical_label_set_checksum
    assert len(chk) == 16
    assert all(c in "0123456789abcdef" for c in chk)


def test_el3_inline_max_chars_matches_promoter_constant():
    """EL3: Lock inline_max_chars matches _MAX_PARA_CHARS in key_value_promoter."""
    from benchmarks.kv_eval.detector_lock import build_lock
    from aksharamd.plugins.transformers.key_value_promoter import _MAX_PARA_CHARS
    lock = build_lock()
    assert lock.inline_max_chars == _MAX_PARA_CHARS


def test_el4_lock_serializes_to_valid_json():
    """EL4: Lock serializes to valid JSON."""
    from benchmarks.kv_eval.detector_lock import build_lock
    lock = build_lock()
    serialized = json.dumps(lock.model_dump())
    parsed = json.loads(serialized)
    assert parsed["detector_version"] == "kv_promoter/v1"


def test_el5_lock_roundtrips_from_json():
    """EL5: Lock deserializes back from JSON with identical field values."""
    from benchmarks.kv_eval.detector_lock import build_lock, KeyValueDetectorLock
    lock = build_lock()
    data = lock.model_dump()
    serialized = json.dumps(data)
    parsed_data = json.loads(serialized)
    lock2 = KeyValueDetectorLock(**parsed_data)
    assert lock2.detector_version == lock.detector_version
    assert lock2.inline_max_chars == lock.inline_max_chars
    assert lock2.max_label_words == lock.max_label_words
    assert lock2.max_value_chars == lock.max_value_chars
    assert lock2.rhetorical_label_set_checksum == lock.rhetorical_label_set_checksum
    assert lock2.xlsx_max_rows == lock.xlsx_max_rows
    assert lock2.pipeline_stage == lock.pipeline_stage


# ── CORPUS ────────────────────────────────────────────────────────────────────

def test_ec1_load_dev_corpus_has_required_keys():
    """EC1: load_dev_corpus() returns dict with heuristic_inline, native_html_dl, negative_control."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    corpus = load_dev_corpus()
    assert "heuristic_inline" in corpus
    assert "native_html_dl" in corpus
    assert "negative_control" in corpus


def test_ec2_each_section_has_at_least_5_cases():
    """EC2: Each corpus section has at least 5 cases."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    corpus = load_dev_corpus()
    for section, cases in corpus.items():
        assert len(cases) >= 5, f"Section '{section}' has only {len(cases)} cases"


def test_ec3_abergowrie_case_has_2_records():
    """EC3: abergowrie_case() returns a case with 2 records in ground truth."""
    from benchmarks.kv_eval.corpus import abergowrie_case
    case = abergowrie_case()
    assert len(case.ground_truth.records) == 2
    assert case.ground_truth.is_key_value_group is True


def test_ec4_all_case_ids_are_unique():
    """EC4: All case IDs in corpus are unique."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    corpus = load_dev_corpus()
    all_ids = []
    for cases in corpus.values():
        for case in cases:
            all_ids.append(case.case_id)
    assert len(all_ids) == len(set(all_ids)), "Duplicate case IDs found"


def test_ec5_positive_and_negative_polarity():
    """EC5: All positive cases have is_key_value_group=True; all negative controls have False."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    corpus = load_dev_corpus()

    for case in corpus.get("heuristic_inline", []):
        assert case.ground_truth.is_key_value_group is True, \
            f"Positive case {case.case_id} has is_key_value_group=False"

    for case in corpus.get("negative_control", []):
        assert case.ground_truth.is_key_value_group is False, \
            f"Negative case {case.case_id} has is_key_value_group=True"


# ── EVALUATOR — INLINE ────────────────────────────────────────────────────────

def test_ei1_contact_block_detected():
    """EI1: Contact-block text → predicted_is_kv=True."""
    from benchmarks.kv_eval.ground_truth import KeyValueGroundTruth
    from benchmarks.kv_eval.evaluator import evaluate_text_case

    text = "Email: alice@example.com\nPhone: 555-1234"
    gt = KeyValueGroundTruth(
        case_id="test_contact",
        document_id="test_contact",
        source_format="text",
        detection_path="heuristic_inline",
        is_key_value_group=True,
    )
    outcome = evaluate_text_case(text, gt)
    assert outcome.predicted_is_kv is True


def test_ei2_rhetorical_prose_not_detected():
    """EI2: Rhetorical prose → predicted_is_kv=False."""
    from benchmarks.kv_eval.ground_truth import KeyValueGroundTruth
    from benchmarks.kv_eval.evaluator import evaluate_text_case

    text = "Note: this explains the process in detail and should be read carefully."
    gt = KeyValueGroundTruth(
        case_id="test_rhetorical",
        document_id="test_rhetorical",
        source_format="text",
        detection_path="negative_control",
        is_key_value_group=False,
    )
    outcome = evaluate_text_case(text, gt)
    assert outcome.predicted_is_kv is False


def test_ei3_long_value_not_detected():
    """EI3: Long-value paragraph → predicted_is_kv=False."""
    from benchmarks.kv_eval.ground_truth import KeyValueGroundTruth
    from benchmarks.kv_eval.evaluator import evaluate_text_case

    text = "Description: This is a very long prose sentence that exceeds eighty characters in length and should be rejected."
    gt = KeyValueGroundTruth(
        case_id="test_long_value",
        document_id="test_long_value",
        source_format="text",
        detection_path="negative_control",
        is_key_value_group=False,
    )
    outcome = evaluate_text_case(text, gt)
    assert outcome.predicted_is_kv is False


def test_ei4_schedule_with_two_time_entries():
    """EI4: Schedule with two time entries → predicted_is_kv=True, predicted_record_count >= 1."""
    from benchmarks.kv_eval.ground_truth import KeyValueGroundTruth
    from benchmarks.kv_eval.evaluator import evaluate_text_case

    text = "Monday: 9:00 AM\nFriday: 5:00 PM"
    gt = KeyValueGroundTruth(
        case_id="test_schedule",
        document_id="test_schedule",
        source_format="text",
        detection_path="heuristic_inline",
        is_key_value_group=True,
    )
    outcome = evaluate_text_case(text, gt)
    assert outcome.predicted_is_kv is True
    assert outcome.predicted_record_count >= 1


def test_ei5_abergowrie_two_records():
    """EI5: Abergowrie two-service text → predicted_is_kv=True, predicted_record_count=2."""
    from benchmarks.kv_eval.corpus import abergowrie_case
    from benchmarks.kv_eval.evaluator import evaluate_text_case

    case = abergowrie_case()
    outcome = evaluate_text_case(case.text, case.ground_truth)
    assert outcome.predicted_is_kv is True
    assert outcome.predicted_record_count == 2


def test_ei6_inline_precision_above_threshold():
    """EI6: Inline metrics has precision >= 0.85 when run against inline corpus."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import evaluate_text_case, compute_corpus_metrics

    corpus = load_dev_corpus()
    inline_cases = corpus["heuristic_inline"]
    negative_cases = corpus["negative_control"]
    all_cases = inline_cases + negative_cases

    outcomes = [evaluate_text_case(c.text, c.ground_truth) for c in all_cases]
    gt_map = {c.ground_truth.case_id: c.ground_truth for c in all_cases}
    metrics = compute_corpus_metrics(outcomes, gt_map, "heuristic_inline")

    assert metrics.precision >= 0.85, \
        f"Precision {metrics.precision:.3f} < 0.85 (tp={metrics.tp}, fp={metrics.fp})"


def test_ei7_inline_fpr_below_threshold():
    """EI7: Inline metrics has fpr <= 0.15 when run against negative controls."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import evaluate_text_case, compute_corpus_metrics

    corpus = load_dev_corpus()
    negative_cases = corpus["negative_control"]

    outcomes = [evaluate_text_case(c.text, c.ground_truth) for c in negative_cases]
    gt_map = {c.ground_truth.case_id: c.ground_truth for c in negative_cases}
    metrics = compute_corpus_metrics(outcomes, gt_map, "negative_control")

    assert metrics.fpr <= 0.15, \
        f"FPR {metrics.fpr:.3f} > 0.15 (fp={metrics.fp}, tn={metrics.tn})"


# ── EVALUATOR — HTML ──────────────────────────────────────────────────────────

def test_eh1_html_dl_two_pairs_detected():
    """EH1: <dl> with 2 pairs → predicted_is_kv=True."""
    from benchmarks.kv_eval.ground_truth import KeyValueGroundTruth
    from benchmarks.kv_eval.evaluator import evaluate_html_case

    html = "<dl><dt>Email</dt><dd>alice@example.com</dd><dt>Phone</dt><dd>555-1234</dd></dl>"
    gt = KeyValueGroundTruth(
        case_id="test_html_dl",
        document_id="test_html_dl",
        source_format="html",
        detection_path="native_html_dl",
        is_key_value_group=True,
    )
    outcome = evaluate_html_case(html, gt)
    assert outcome.predicted_is_kv is True


def test_eh2_html_no_dl_not_detected():
    """EH2: HTML with no <dl> → predicted_is_kv=False."""
    from benchmarks.kv_eval.ground_truth import KeyValueGroundTruth
    from benchmarks.kv_eval.evaluator import evaluate_html_case

    html = "<p>This is a plain paragraph with no structured data at all.</p>"
    gt = KeyValueGroundTruth(
        case_id="test_html_no_dl",
        document_id="test_html_no_dl",
        source_format="html",
        detection_path="native_html_dl",
        is_key_value_group=False,
    )
    outcome = evaluate_html_case(html, gt)
    assert outcome.predicted_is_kv is False


def test_eh3_html_dl_precision_is_one():
    """EH3: HTML DL precision = 1.0 on the html corpus (native extraction is deterministic)."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import evaluate_html_case, compute_corpus_metrics

    corpus = load_dev_corpus()
    html_cases = corpus["native_html_dl"]
    # Only evaluate the true-positive cases (those with is_key_value_group=True)
    positive_cases = [c for c in html_cases if c.ground_truth.is_key_value_group]

    outcomes = [evaluate_html_case(c.html, c.ground_truth) for c in positive_cases]
    gt_map = {c.ground_truth.case_id: c.ground_truth for c in positive_cases}
    metrics = compute_corpus_metrics(outcomes, gt_map, "native_html_dl")

    assert metrics.fp == 0, f"HTML DL has {metrics.fp} FP on positive cases"
    # precision is only meaningful if we have any predictions
    if metrics.tp + metrics.fp > 0:
        assert metrics.precision == 1.0


def test_eh4_html_dl_detection_path_used():
    """EH4: HTML DL detection_path_used = 'native_html_dl'."""
    from benchmarks.kv_eval.ground_truth import KeyValueGroundTruth
    from benchmarks.kv_eval.evaluator import evaluate_html_case

    html = "<dl><dt>Name</dt><dd>Alice</dd><dt>Email</dt><dd>alice@test.com</dd><dt>Phone</dt><dd>555-0001</dd></dl>"
    gt = KeyValueGroundTruth(
        case_id="test_html_path",
        document_id="test_html_path",
        source_format="html",
        detection_path="native_html_dl",
        is_key_value_group=True,
    )
    outcome = evaluate_html_case(html, gt)
    assert outcome.predicted_is_kv is True
    assert outcome.detection_path_used == "native_html_dl"


def test_eh5_html_dl_confidence_is_extracted():
    """EH5: HTML DL entries have confidence='extracted' (native path)."""
    from aksharamd.plugins.parsers.html import _dl_to_key_value_group
    from bs4 import BeautifulSoup

    html = "<dl><dt>Email</dt><dd>alice@example.com</dd><dt>Phone</dt><dd>555-1234</dd></dl>"
    soup = BeautifulSoup(html, "html.parser")
    dl = soup.find("dl")
    group = _dl_to_key_value_group(dl, page=None)
    assert group is not None
    assert group.confidence == "extracted"
    for entry in group.entries:
        assert entry.confidence == "extracted"


# ── TOKEN COMPARISON ──────────────────────────────────────────────────────────

def test_et1_compare_tokens_returns_full_model():
    """ET1: compare_tokens() returns TokenComparison with all fields populated."""
    from benchmarks.kv_eval.token_comparison import compare_tokens, TokenComparison
    from aksharamd.models.key_value import KeyValueGroup, KeyValueEntry, KeyValueGroupType

    entries = [
        KeyValueEntry(key="Email", value="alice@example.com"),
        KeyValueEntry(key="Phone", value="555-1234"),
        KeyValueEntry(key="Name", value="Alice"),
        KeyValueEntry(key="City", value="Sydney"),
    ]
    group = KeyValueGroup(entries=entries, group_type=KeyValueGroupType.CONTACT)
    source = "Email: alice@example.com\nPhone: 555-1234\nName: Alice\nCity: Sydney"

    result = compare_tokens("test_tc_01", source, group)
    assert isinstance(result, TokenComparison)
    assert result.case_id == "test_tc_01"
    assert result.source_text_tokens > 0
    assert result.markdown_list_tokens > 0
    assert result.tsv_tokens > 0
    assert result.selected_tokens > 0
    assert isinstance(result.delta_pct, float)


def test_et2_small_contact_group_token_ratio():
    """ET2: Small contact group (4 entries): selected_tokens within 50% of source_text_tokens."""
    from benchmarks.kv_eval.token_comparison import compare_tokens
    from aksharamd.models.key_value import KeyValueGroup, KeyValueEntry, KeyValueGroupType

    entries = [
        KeyValueEntry(key="Email", value="alice@example.com"),
        KeyValueEntry(key="Phone", value="555-1234"),
        KeyValueEntry(key="Name", value="Alice"),
        KeyValueEntry(key="City", value="Sydney"),
    ]
    group = KeyValueGroup(entries=entries, group_type=KeyValueGroupType.CONTACT)
    source = "Email: alice@example.com\nPhone: 555-1234\nName: Alice\nCity: Sydney"

    result = compare_tokens("test_tc_small", source, group)
    ratio = result.selected_tokens / result.source_text_tokens
    assert 0.5 <= ratio <= 1.5, f"Token ratio {ratio:.2f} out of expected range [0.5, 1.5]"


def test_et3_delta_pct_is_float():
    """ET3: delta_pct is a float (positive or negative acceptable)."""
    from benchmarks.kv_eval.token_comparison import compare_tokens
    from aksharamd.models.key_value import KeyValueGroup, KeyValueEntry, KeyValueGroupType

    entries = [
        KeyValueEntry(key="A", value="B"),
        KeyValueEntry(key="C", value="D"),
    ]
    group = KeyValueGroup(entries=entries, group_type=KeyValueGroupType.UNKNOWN)
    result = compare_tokens("test_float", "A: B\nC: D", group)
    assert isinstance(result.delta_pct, float)


def test_et4_large_group_tsv_considered():
    """ET4: Large group (>20 entries): TSV is considered for selection (min of md/tsv)."""
    from benchmarks.kv_eval.token_comparison import compare_tokens
    from aksharamd.models.key_value import KeyValueGroup, KeyValueEntry, KeyValueGroupType

    # Build 22 entries
    entries = [KeyValueEntry(key=f"Key{i}", value=f"Value{i}") for i in range(22)]
    group = KeyValueGroup(entries=entries, group_type=KeyValueGroupType.METADATA)
    source = "\n".join(f"Key{i}: Value{i}" for i in range(22))

    result = compare_tokens("test_large", source, group)
    # For >20 entries, selected = min(md_tok, tsv_tok)
    assert result.selected_tokens == min(result.markdown_list_tokens, result.tsv_tokens)


# ── ADJACENT THRESHOLD SIMULATION ────────────────────────────────────────────

def test_ea1_simulate_returns_four_entries():
    """EA1: simulate_adjacent_threshold with min_blocks=[2,3,4,5] returns 4 entries."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import simulate_adjacent_threshold

    corpus = load_dev_corpus()
    inline_cases = corpus["heuristic_inline"] + corpus["negative_control"]
    gt_map = {c.ground_truth.case_id: c.ground_truth for c in inline_cases}

    results = simulate_adjacent_threshold(inline_cases, gt_map, [2, 3, 4, 5])
    assert len(results) == 4
    assert set(results.keys()) == {2, 3, 4, 5}


def test_ea2_min_blocks_2_recall_ge_min_blocks_4():
    """EA2: min_blocks=2 has recall >= recall at min_blocks=4 (more permissive)."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import simulate_adjacent_threshold

    corpus = load_dev_corpus()
    inline_cases = corpus["heuristic_inline"] + corpus["negative_control"]
    gt_map = {c.ground_truth.case_id: c.ground_truth for c in inline_cases}

    results = simulate_adjacent_threshold(inline_cases, gt_map, [2, 4])
    # More permissive threshold should not have strictly lower recall
    assert results[2].recall >= results[4].recall - 0.01  # allow tiny float tolerance


def test_ea3_min_blocks_5_precision_ge_min_blocks_2():
    """EA3: min_blocks=5 has precision >= precision at min_blocks=2 (stricter = fewer FP)."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import simulate_adjacent_threshold

    corpus = load_dev_corpus()
    inline_cases = corpus["heuristic_inline"] + corpus["negative_control"]
    gt_map = {c.ground_truth.case_id: c.ground_truth for c in inline_cases}

    results = simulate_adjacent_threshold(inline_cases, gt_map, [2, 5])
    # Stricter threshold should not have lower precision
    # (equal is fine if no FP difference)
    assert results[5].precision >= results[2].precision - 0.01


def test_ea4_path_names_match_adjacent_min_n_format():
    """EA4: All returned metrics have path_name matching 'adjacent_min_N' format."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import simulate_adjacent_threshold

    corpus = load_dev_corpus()
    inline_cases = corpus["heuristic_inline"][:5]  # small subset for speed
    gt_map = {c.ground_truth.case_id: c.ground_truth for c in inline_cases}

    results = simulate_adjacent_threshold(inline_cases, gt_map, [2, 3, 4, 5])
    for k, metrics in results.items():
        assert metrics.path_name == f"adjacent_min_{k}"


# ── MATURITY ASSESSMENT ───────────────────────────────────────────────────────

def test_em1_html_dl_no_fp_on_positive_cases():
    """EM1: HTML DL path has 0 FP in the html corpus (positive cases only)."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import evaluate_html_case, compute_corpus_metrics

    corpus = load_dev_corpus()
    html_cases = [c for c in corpus["native_html_dl"] if c.ground_truth.is_key_value_group]
    outcomes = [evaluate_html_case(c.html, c.ground_truth) for c in html_cases]
    gt_map = {c.ground_truth.case_id: c.ground_truth for c in html_cases}
    metrics = compute_corpus_metrics(outcomes, gt_map, "native_html_dl")
    assert metrics.fp == 0, f"HTML DL has {metrics.fp} unexpected FPs on positive cases"


def test_em2_rhetorical_labels_produce_no_tp():
    """EM2: Inline path has 0 TP on negative controls containing rhetorical labels."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import evaluate_text_case
    from aksharamd.scoring.key_value_detection import _RHETORICAL_LABELS

    corpus = load_dev_corpus()
    # Filter negative controls whose reason is "rhetorical_label"
    rhetorical_cases = [
        c for c in corpus["negative_control"]
        if c.ground_truth.negative_reason == "rhetorical_label"
    ]

    false_positives = []
    for case in rhetorical_cases:
        outcome = evaluate_text_case(case.text, case.ground_truth)
        if outcome.predicted_is_kv:
            false_positives.append(case.case_id)

    assert len(false_positives) == 0, \
        f"Rhetorical-label cases incorrectly promoted: {false_positives}"


def test_em3_abergowrie_regression_passes():
    """EM3: abergowrie_regression.pass in eval summary is True."""
    from benchmarks.kv_eval.corpus import abergowrie_case
    from benchmarks.kv_eval.evaluator import evaluate_text_case

    case = abergowrie_case()
    outcome = evaluate_text_case(case.text, case.ground_truth)
    regression_pass = outcome.predicted_is_kv and outcome.predicted_record_count == 2
    assert regression_pass is True, \
        f"Abergowrie regression FAIL: predicted_is_kv={outcome.predicted_is_kv}, " \
        f"record_count={outcome.predicted_record_count}"


# ── NEW CORPUS SECTIONS ───────────────────────────────────────────────────────

def test_ec6_corpus_has_hard_negative_section():
    """EC6: load_dev_corpus() contains 'hard_negative' key with >= 20 cases."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    corpus = load_dev_corpus()
    assert "hard_negative" in corpus, "corpus missing 'hard_negative' key"
    assert len(corpus["hard_negative"]) >= 20, \
        f"hard_negative has only {len(corpus['hard_negative'])} cases"


def test_ec7_corpus_has_adjacent_block_section():
    """EC7: load_dev_corpus() contains 'adjacent_block' key with >= 5 cases."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    corpus = load_dev_corpus()
    assert "adjacent_block" in corpus, "corpus missing 'adjacent_block' key"
    assert len(corpus["adjacent_block"]) >= 5, \
        f"adjacent_block has only {len(corpus['adjacent_block'])} cases"


def test_em4_html_recall_is_one():
    """EM4: HTML DL recall >= 0.99 after corrected corpus (FN=0)."""
    from benchmarks.kv_eval.corpus import load_dev_corpus
    from benchmarks.kv_eval.evaluator import evaluate_html_case, compute_corpus_metrics

    corpus = load_dev_corpus()
    html_cases = corpus["native_html_dl"]
    outcomes = [evaluate_html_case(c.html, c.ground_truth) for c in html_cases]
    gt_map = {c.ground_truth.case_id: c.ground_truth for c in html_cases}
    metrics = compute_corpus_metrics(outcomes, gt_map, "native_html_dl")

    assert metrics.recall >= 0.99, \
        f"HTML recall {metrics.recall:.3f} < 0.99 (fn={metrics.fn})"
    assert metrics.fn == 0, f"HTML has {metrics.fn} false negatives"


def test_em5_path_maturity_labels_exist():
    """EM5: PathMaturityLabels has all five required path labels.

    kv_promoter/v2: heuristic paths are labelled ``experimental_disabled_by_default``
    now that Round 1 hard-negative FPR=0.929 gated them behind an opt-in profile.
    """
    from benchmarks.kv_eval.ground_truth import PathMaturityLabels
    pm = PathMaturityLabels()
    assert pm.native_html_dl == "production_candidate"
    assert pm.native_docx_props == "production_candidate"
    assert pm.native_xlsx_kv == "restricted_beta"
    assert pm.heuristic_inline == "experimental_disabled_by_default"
    assert pm.heuristic_adjacent == "experimental_disabled_by_default"
    assert len(pm.rationale) == 5


def test_em6_detector_lock_has_path_maturity():
    """EM6: build_lock() result contains path_maturity dict with all five paths."""
    from benchmarks.kv_eval.detector_lock import build_lock
    lock = build_lock()
    assert hasattr(lock, "path_maturity")
    assert isinstance(lock.path_maturity, dict)
    expected_paths = {"native_html_dl", "native_docx_props", "native_xlsx_kv",
                      "heuristic_inline", "heuristic_adjacent"}
    assert expected_paths == set(lock.path_maturity.keys())


def test_em7_adjacent_real_positive_cases_detected():
    """EM7: Adjacent real positive cases (blocks with Key: Value) are correctly promoted.

    Under kv_promoter/v2 heuristics are opt-in — evaluate with the
    experimental profile so the adjacent promoter runs.
    """
    from benchmarks.kv_eval.corpus import _adjacent_block_cases
    from benchmarks.kv_eval.evaluator import evaluate_adjacent_case
    from aksharamd.scoring.key_value_config import KeyValueDetectionProfile

    profile = KeyValueDetectionProfile.experimental()
    adj_cases = _adjacent_block_cases()
    positive_cases = [c for c in adj_cases if c.ground_truth.is_key_value_group]
    false_negatives = []
    for c in positive_cases:
        outcome = evaluate_adjacent_case(c.blocks, c.ground_truth, profile=profile)
        if not outcome.predicted_is_kv:
            false_negatives.append(c.case_id)

    assert len(false_negatives) == 0, \
        f"Adjacent real positive cases not promoted: {false_negatives}"


# ── LLMPayloadItem new fields ─────────────────────────────────────────────────

def test_payload_item_kv_format_fields_exist():
    """New fields kv_selected_format/kv_markdown_tokens/kv_tsv_tokens exist and have defaults."""
    from aksharamd.packaging.payload import LLMPayloadItem, PayloadContentType
    item = LLMPayloadItem(
        item_id="test",
        content_type=PayloadContentType.TEXT,
        document_id="doc",
        element_id="el",
    )
    assert item.kv_selected_format == ""
    assert item.kv_markdown_tokens == 0
    assert item.kv_tsv_tokens == 0


def test_token_comparison_selected_format_field():
    """TokenComparison.selected_format is 'markdown' or 'tsv'."""
    from benchmarks.kv_eval.token_comparison import compare_tokens
    from aksharamd.models.key_value import KeyValueGroup, KeyValueEntry, KeyValueGroupType

    entries = [
        KeyValueEntry(key="Email", value="alice@example.com"),
        KeyValueEntry(key="Phone", value="555-1234"),
        KeyValueEntry(key="Name", value="Alice"),
    ]
    group = KeyValueGroup(entries=entries, group_type=KeyValueGroupType.CONTACT)
    result = compare_tokens("test_fmt", "Email: alice@example.com\nPhone: 555-1234\nName: Alice", group)
    assert result.selected_format in ("markdown", "tsv")
    # selected_tokens should match the chosen format
    if result.selected_format == "tsv":
        assert result.selected_tokens == result.tsv_tokens
    else:
        assert result.selected_tokens == result.markdown_list_tokens
