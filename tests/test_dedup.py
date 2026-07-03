"""Tests for aksharamd.dedup.minhash — MinHash + LSH corpus deduplication."""
from __future__ import annotations

import pytest

from aksharamd.dedup.minhash import CorpusDeduplicator, _lsh_params, _shingles, minhash


# ── _shingles ─────────────────────────────────────────────────────────────────

def test_shingles_short_text_returns_single_entry():
    result = _shingles("hello world")
    assert len(result) == 1
    assert isinstance(result[0], bytes)


def test_shingles_empty_text_returns_single_entry():
    result = _shingles("")
    assert len(result) == 1


def test_shingles_exactly_k_words():
    result = _shingles("the quick brown fox jumps")
    assert len(result) == 1


def test_shingles_more_than_k_words():
    text = "the quick brown fox jumps over the lazy dog"
    result = _shingles(text)
    words = text.lower().split()
    assert len(result) == len(words) - 5 + 1


def test_shingles_are_bytes():
    result = _shingles("one two three four five six")
    assert all(isinstance(s, bytes) for s in result)


def test_shingles_lowercases_text():
    lower = _shingles("hello world foo bar baz")
    upper = _shingles("HELLO WORLD FOO BAR BAZ")
    assert lower == upper


# ── minhash ───────────────────────────────────────────────────────────────────

def test_minhash_returns_correct_length():
    sig = minhash("some text here", num_perm=32)
    assert len(sig) == 32


def test_minhash_identical_texts_produce_equal_signatures():
    text = "the quick brown fox jumps over the lazy dog " * 5
    assert minhash(text) == minhash(text)


def test_minhash_different_texts_differ():
    sig_a = minhash("machine learning and deep neural networks " * 5)
    sig_b = minhash("cooking recipes and food preparation guides " * 5)
    assert sig_a != sig_b


def test_minhash_empty_string():
    sig = minhash("")
    assert len(sig) == 64
    assert all(isinstance(v, int) for v in sig)


def test_minhash_similar_texts_share_many_values():
    base = "the quick brown fox jumps over the lazy dog " * 10
    variant = base + " extra words at end"
    sig_a = minhash(base)
    sig_b = minhash(variant)
    matches = sum(a == b for a, b in zip(sig_a, sig_b))
    assert matches > 20


# ── _lsh_params ───────────────────────────────────────────────────────────────

def test_lsh_params_returns_valid_band_row():
    b, r = _lsh_params(0.5, 64)
    assert b * r == 64
    assert b >= 1 and r >= 1


def test_lsh_params_different_thresholds_differ():
    b_low, r_low = _lsh_params(0.3, 64)
    b_high, r_high = _lsh_params(0.9, 64)
    # Both must divide num_perm evenly; they should differ
    assert b_low * r_low == 64
    assert b_high * r_high == 64
    assert (b_low, r_low) != (b_high, r_high)


# ── CorpusDeduplicator ────────────────────────────────────────────────────────

@pytest.fixture
def dd():
    return CorpusDeduplicator(threshold=0.8, num_perm=64)


def test_first_document_returns_no_duplicates(dd):
    result = dd.add("doc1", "the quick brown fox jumps over the lazy dog " * 10)
    assert result == []


def test_identical_document_detected_as_duplicate(dd):
    text = "the quick brown fox jumps over the lazy dog " * 20
    dd.add("doc1", text)
    dupes = dd.add("doc2", text)
    assert "doc1" in dupes


def test_very_different_document_not_a_duplicate(dd):
    dd.add("doc1", "the quick brown fox jumps over the lazy dog " * 20)
    dupes = dd.add("doc2", "completely unrelated text about cooking and recipes " * 20)
    assert dupes == []


def test_self_not_returned_as_duplicate(dd):
    text = "some repeated text content " * 20
    dd.add("doc1", text)
    dupes = dd.add("doc1", text)
    assert "doc1" not in dupes


def test_indexed_count_increments(dd):
    assert dd.indexed_count == 0
    dd.add("a", "text " * 20)
    assert dd.indexed_count == 1
    dd.add("b", "different text " * 20)
    assert dd.indexed_count == 2


def test_already_seen_returns_true_for_duplicate(dd):
    text = "the quick brown fox jumps over the lazy dog " * 20
    dd.add("original", text)
    assert dd.already_seen("copy", text) is True


def test_already_seen_returns_false_for_novel(dd):
    dd.add("doc1", "some text about machine learning " * 20)
    assert dd.already_seen("doc2", "completely different cooking content " * 20) is False


def test_signature_length_matches_num_perm(dd):
    sig = dd.signature("hello world " * 10)
    assert len(sig) == 64


def test_low_threshold_catches_more_duplicates():
    strict = CorpusDeduplicator(threshold=0.9)
    lenient = CorpusDeduplicator(threshold=0.3)
    text_a = "the quick brown fox jumps over the lazy dog " * 20
    text_b = "the quick brown fox jumps over the lazy cat " * 20
    strict.add("a", text_a)
    lenient.add("a", text_a)
    strict_dupes = strict.add("b", text_b)
    lenient_dupes = lenient.add("b", text_b)
    assert len(lenient_dupes) >= len(strict_dupes)


def test_multiple_documents_all_indexed():
    dd = CorpusDeduplicator(threshold=0.5)
    for i in range(10):
        dd.add(f"doc{i}", f"document number {i} with unique content about topic {i} " * 5)
    assert dd.indexed_count == 10
