"""Tests for aksharamd.ledger — persistent compilation ledger."""
from __future__ import annotations

import json

import pytest

from aksharamd import ledger

# _isolate_ledger in conftest.py is autouse=True, so ledger writes go to tmp_path.


def test_read_entries_empty_when_no_file():
    assert ledger.read_entries() == []


def test_append_then_read_single_entry():
    ledger.append_entry("doc.md", "md", 500, 300, 1.2)
    entries = ledger.read_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e.source == "doc.md"
    assert e.file_type == "md"
    assert e.original_tokens == 500
    assert e.optimized_tokens == 300
    assert e.saved_tokens == 200
    assert e.elapsed_seconds == pytest.approx(1.2)


def test_append_multiple_entries():
    ledger.append_entry("a.txt", "txt", 100, 80, 0.5)
    ledger.append_entry("b.pdf", "pdf", 2000, 1500, 3.0)
    entries = ledger.read_entries()
    assert len(entries) == 2
    assert {e.source for e in entries} == {"a.txt", "b.pdf"}


def test_expansion_records_negative_savings():
    ledger.append_entry("small.md", "md", 100, 200, 0.1)
    entries = ledger.read_entries()
    assert entries[0].saved_tokens == -100


def test_get_stats_empty_returns_empty_dict():
    assert ledger.get_stats() == {}


def test_get_stats_single_entry():
    ledger.append_entry("report.pdf", "pdf", 1000, 600, 2.0)
    stats = ledger.get_stats()
    assert stats["total_compilations"] == 1
    assert stats["total_original_tokens"] == 1000
    assert stats["total_optimized_tokens"] == 600
    assert stats["total_saved_tokens"] == 400
    assert stats["reduction_percent"] == 40.0
    assert "pdf" in stats["by_file_type"]
    assert stats["by_file_type"]["pdf"]["count"] == 1
    assert stats["by_file_type"]["pdf"]["saved"] == 400


def test_get_stats_multiple_file_types():
    ledger.append_entry("a.md", "md", 500, 400, 1.0)
    ledger.append_entry("b.md", "md", 300, 250, 0.8)
    ledger.append_entry("c.pdf", "pdf", 800, 500, 2.5)
    stats = ledger.get_stats()
    assert stats["total_compilations"] == 3
    assert stats["by_file_type"]["md"]["count"] == 2
    assert stats["by_file_type"]["pdf"]["count"] == 1


def test_get_stats_recent_capped_at_10():
    for i in range(15):
        ledger.append_entry(f"doc{i}.txt", "txt", 100, 80, 0.1)
    stats = ledger.get_stats()
    assert len(stats["recent"]) == 10


def test_read_entries_skips_malformed_lines(tmp_path, monkeypatch):

    fake_dir = tmp_path / ".aksharamd"
    fake_dir.mkdir()
    fake_file = fake_dir / "ledger.jsonl"

    valid = json.dumps({
        "ts": "2026-01-01T00:00:00+00:00",
        "source": "ok.md",
        "file_type": "md",
        "original_tokens": 100,
        "optimized_tokens": 80,
        "saved_tokens": 20,
        "elapsed_seconds": 0.5,
    })
    fake_file.write_text("not valid json\n\n" + valid + "\n", encoding="utf-8")

    monkeypatch.setattr(ledger, "_LEDGER_DIR", fake_dir)
    monkeypatch.setattr(ledger, "_LEDGER_FILE", fake_file)

    entries = ledger.read_entries()
    assert len(entries) == 1
    assert entries[0].source == "ok.md"


def test_ledger_entry_ts_is_string():
    ledger.append_entry("x.txt", "txt", 10, 8, 0.01)
    entries = ledger.read_entries()
    assert isinstance(entries[0].ts, str)
    assert entries[0].ts  # non-empty


def test_mixed_savings_and_expansion_reconcile():
    ledger.append_entry("a.md", "md", 100, 50, 0.1)
    ledger.append_entry("b.md", "md", 100, 200, 0.1)
    stats = ledger.get_stats()
    assert stats["total_saved_tokens"] == -50
    assert stats["total_saved_tokens"] == (
        stats["total_original_tokens"] - stats["total_optimized_tokens"]
    )
    assert stats["by_file_type"]["md"]["saved"] == -50
    assert stats["reduction_percent"] == -25.0


def test_historical_clipped_expansion_is_corrected_on_read():

    ledger.append_entry("old.md", "md", 100, 200, 0.1)
    path = ledger._ledger_path()
    old_record = json.loads(path.read_text(encoding="utf-8"))
    old_record["saved_tokens"] = 0
    path.write_text(json.dumps(old_record) + "\n", encoding="utf-8")
    assert ledger.read_entries()[0].saved_tokens == -100
    assert ledger.get_stats()["recent"][0]["saved_tokens"] == -100
    assert ledger.get_stats()["total_saved_tokens"] == -100
    assert json.loads(path.read_text(encoding="utf-8"))["saved_tokens"] == 0
