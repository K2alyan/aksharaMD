"""Tests for IndexQueue — SQLite job queue with content-hash dedup."""
from __future__ import annotations

import pytest

from aksharamd.index.queue import IndexQueue


def _make_file(tmp_path, name: str, content: bytes = b"fake content") -> str:
    f = tmp_path / name
    f.write_bytes(content)
    return str(f)


def test_enqueue_new_file(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path, "a.pdf")
    assert q.enqueue(path) is True
    jobs = q.list_all()
    assert len(jobs) == 1
    assert jobs[0].status == "pending"
    assert jobs[0].path == path


def test_skip_unchanged_done(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path, "a.pdf")
    q.enqueue(path)
    dequeued = q.dequeue()
    q.mark_done(dequeued, chunk_count=5)
    # Same file, same content — should be skipped
    assert q.enqueue(path) is False
    assert len(q.list_all(status="done")) == 1


def test_requeue_on_changed_content(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path, "a.pdf", b"original")
    q.enqueue(path)
    q.mark_done(q.dequeue(), chunk_count=3)

    # Update the file bytes
    (tmp_path / "a.pdf").write_bytes(b"updated content")
    assert q.enqueue(path) is True
    jobs = q.list_all(status="pending")
    assert len(jobs) == 1


def test_dequeue_empty_returns_none(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    assert q.dequeue() is None


def test_status_transitions(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path, "a.pdf")
    q.enqueue(path)

    p = q.dequeue()
    assert p == path
    assert q.list_all(status="processing")[0].path == path

    q.mark_done(p, chunk_count=7)
    done = q.list_all(status="done")
    assert len(done) == 1
    assert done[0].chunk_count == 7


def test_mark_low_quality(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path, "a.pdf")
    q.enqueue(path)
    q.mark_low_quality(q.dequeue(), readiness_score=30)
    jobs = q.list_all(status="low_quality")
    assert len(jobs) == 1
    assert jobs[0].readiness_score == 30


def test_mark_error(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path, "a.pdf")
    q.enqueue(path)
    q.mark_error(q.dequeue(), "something went wrong")
    jobs = q.list_all(status="error")
    assert len(jobs) == 1
    assert "something went wrong" in jobs[0].error


def test_stats(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    p1 = _make_file(tmp_path, "a.pdf", b"aaa")
    p2 = _make_file(tmp_path, "b.pdf", b"bbb")
    q.enqueue(p1)
    q.enqueue(p2)
    q.mark_done(q.dequeue(), chunk_count=3)
    stats = q.stats()
    assert stats["pending"] == 1
    assert stats["done"] == 1


def test_remove(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path, "a.pdf")
    q.enqueue(path)
    q.remove(path)
    assert len(q.list_all()) == 0


def test_enqueue_missing_file_raises(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    with pytest.raises(FileNotFoundError):
        q.enqueue(str(tmp_path / "does_not_exist.pdf"))


def test_pending_count(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    for i in range(3):
        path = _make_file(tmp_path, f"{i}.pdf", f"content{i}".encode())
        q.enqueue(path)
    assert q.pending_count() == 3
    q.dequeue()
    assert q.pending_count() == 2
