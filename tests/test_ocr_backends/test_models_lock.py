"""Tests for the Unlimited-OCR model filesystem lock (PR 98).

The lock protects install/remove. These tests cover:

- fresh acquisition writes structured JSON metadata
- stale lock (age > threshold) is broken and re-acquired
- live-PID lock is NOT broken even if age > threshold
- malformed lock contents are treated conservatively (not broken)
- lock released in ``finally`` even on exception
- concurrent acquisition: second raises ``LockHeld``
"""
from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path

import pytest

from aksharamd.plugins.ocr_backends.unlimited_ocr._lock import (
    LockHeld,
    model_lock,
)


def _write_lock_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# ── Fresh acquisition ─────────────────────────────────────────────────────


def test_fresh_acquisition_writes_json_metadata(tmp_path: Path) -> None:
    lock_path = tmp_path / "m.lock"
    with model_lock(lock_path, operation="install"):
        assert lock_path.exists()
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert payload["operation"] == "install"
        assert "created_at" in payload
        assert "hostname" in payload
        assert isinstance(payload["hostname"], str)
    assert not lock_path.exists()


def test_lock_released_in_finally_on_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "m.lock"
    with pytest.raises(RuntimeError, match="boom"):
        with model_lock(lock_path, operation="install"):
            assert lock_path.exists()
            raise RuntimeError("boom")
    assert not lock_path.exists()


def test_unknown_operation_rejected(tmp_path: Path) -> None:
    lock_path = tmp_path / "m.lock"
    with pytest.raises(ValueError):
        with model_lock(lock_path, operation="verify"):  # not allowed
            pass


# ── Stale detection: age ──────────────────────────────────────────────────


def test_stale_lock_broken_and_reacquired(tmp_path: Path) -> None:
    lock_path = tmp_path / "m.lock"
    # Simulate an old lock: created 2 hours ago on a foreign host so
    # the PID-liveness path is not consulted.
    old_time = time.gmtime(time.time() - 7200)
    _write_lock_file(lock_path, {
        "pid": 999999,
        "hostname": "some-other-host-that-is-not-us",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", old_time),
        "operation": "install",
    })
    with model_lock(lock_path, operation="install", stale_seconds=900):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
    assert not lock_path.exists()


def test_stale_lock_from_dead_pid_on_same_host(tmp_path: Path) -> None:
    lock_path = tmp_path / "m.lock"
    # Use a PID very unlikely to be alive: max unsigned 32-bit minus a
    # bit. On both POSIX and Windows this yields "no such process".
    _write_lock_file(lock_path, {
        "pid": 4_293_000_000,
        "hostname": platform.node(),
        # Recent so the age heuristic does NOT trigger — only the
        # PID-liveness path.
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "operation": "install",
    })
    with model_lock(lock_path, operation="install", stale_seconds=900):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()


# ── Live-PID lock is NOT broken ───────────────────────────────────────────


def test_live_pid_lock_not_broken_even_when_recent(tmp_path: Path) -> None:
    lock_path = tmp_path / "m.lock"
    # Use our own PID as the "holder"; it is definitely alive. Recent
    # timestamp so age does not force a break.
    _write_lock_file(lock_path, {
        "pid": os.getpid(),
        "hostname": platform.node(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "operation": "install",
    })
    with pytest.raises(LockHeld) as excinfo:
        with model_lock(lock_path, operation="install", stale_seconds=900):
            pass
    assert excinfo.value.holder is not None
    assert excinfo.value.holder["pid"] == os.getpid()


# ── Malformed lock is treated conservatively ──────────────────────────────


def test_malformed_lock_treated_as_active(tmp_path: Path) -> None:
    lock_path = tmp_path / "m.lock"
    lock_path.write_text("this is not JSON at all", encoding="utf-8")
    with pytest.raises(LockHeld) as excinfo:
        with model_lock(lock_path, operation="install", stale_seconds=1):
            pass
    # holder is None because we could not parse it
    assert excinfo.value.holder is None
    # Lock file was NOT removed by the failed acquisition.
    assert lock_path.exists()


def test_partial_json_lock_treated_as_active(tmp_path: Path) -> None:
    lock_path = tmp_path / "m.lock"
    # Missing required 'operation' key.
    lock_path.write_text(json.dumps({
        "pid": 1,
        "hostname": "x",
        "created_at": "2026-07-21T14:00:00Z",
    }), encoding="utf-8")
    with pytest.raises(LockHeld):
        with model_lock(lock_path, operation="install", stale_seconds=1):
            pass


# ── Concurrent acquisition: second raises LockHeld ────────────────────────


def test_concurrent_acquisition_second_raises(tmp_path: Path) -> None:
    lock_path = tmp_path / "m.lock"
    with model_lock(lock_path, operation="install"):
        with pytest.raises(LockHeld):
            with model_lock(
                lock_path, operation="remove", stale_seconds=900,
            ):
                pass
    # After the outer releases, the inner attempt would succeed.
    with model_lock(lock_path, operation="remove"):
        assert lock_path.exists()


def test_two_threads_serialize(tmp_path: Path) -> None:
    """Simulate contention: one holder + another waiting attempt.

    We do not spin waiters (the lock has no wait/timeout API — the
    contract is fail-fast with LockHeld). This test exercises the
    contention path directly by starting a second attempt from a
    thread that grabs the exception.
    """
    import threading

    lock_path = tmp_path / "m.lock"
    barrier = threading.Barrier(2)
    outcome: dict[str, object] = {}

    def worker() -> None:
        barrier.wait()
        try:
            with model_lock(lock_path, operation="remove", stale_seconds=900):
                outcome["ok"] = True
        except LockHeld as exc:
            outcome["held"] = True
            outcome["holder_pid"] = exc.holder["pid"] if exc.holder else None

    t = threading.Thread(target=worker)
    with model_lock(lock_path, operation="install"):
        t.start()
        barrier.wait()
        t.join(timeout=5)
    assert outcome.get("held") is True
    assert outcome.get("holder_pid") == os.getpid()
