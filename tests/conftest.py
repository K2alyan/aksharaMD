from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_ledger(monkeypatch, tmp_path):
    """Redirect ledger writes to a temp dir so tests don't pollute ~/.aksharamd/."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))  # Windows

    # The ledger module caches Path.home() at import time, so also patch
    # the module-level constants directly to guarantee isolation.
    import aksharamd.ledger as _ledger

    fake_ledger_dir = fake_home / ".aksharamd"
    fake_ledger_file = fake_ledger_dir / "ledger.jsonl"
    monkeypatch.setattr(_ledger, "_LEDGER_DIR", fake_ledger_dir)
    monkeypatch.setattr(_ledger, "_LEDGER_FILE", fake_ledger_file)
