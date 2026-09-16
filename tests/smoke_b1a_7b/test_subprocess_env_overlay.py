"""Regression tests for the subprocess env inheritance-plus-overlay fix.

Attempt #3 of the real smoke produced 12 uniform ``<parser>_exception:OSError``
DEFECTs in ~0.2s each. Root cause: ``RealSubprocessInvoker.invoke``
used ``env = dict(os.environ if invocation.env is None else invocation.env)``,
which REPLACED the entire environment when ``invocation.env`` was set.
Under an elevated PowerShell smoke run, the parser workers received
only ``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE`` /
``DOCLING_ARTIFACTS_OFFLINE`` — no ``SYSTEMROOT`` / ``PATH`` /
``TEMP`` / ``USERPROFILE``. On Windows this ``OSError``s the child
before any real parser work happens.

The corrected semantic is INHERITANCE-PLUS-OVERLAY: the child gets
``os.environ`` merged with the offline overrides, with the overrides
winning.
"""
from __future__ import annotations

import os

from benchmarks.eval_v1.smoke_b1a_7b.subprocess_runner import (
    compose_child_env,
)


def test_overlay_inherits_all_parent_keys() -> None:
    parent = {"PATH": "C:\\Windows;C:\\Windows\\system32",
              "SYSTEMROOT": "C:\\Windows",
              "USERPROFILE": "C:\\Users\\kalya",
              "TEMP": "C:\\Users\\kalya\\AppData\\Local\\Temp",
              "SOME_OTHER_VAR": "arbitrary parent value"}
    overlay = {"HF_HUB_OFFLINE": "1"}
    child = compose_child_env(parent, overlay)
    for k in ("PATH", "SYSTEMROOT", "USERPROFILE", "TEMP", "SOME_OTHER_VAR"):
        assert k in child
        assert child[k] == parent[k]


def test_overlay_adds_offline_keys() -> None:
    parent = {"SYSTEMROOT": "C:\\Windows"}
    overlay = {"HF_HUB_OFFLINE": "1",
               "TRANSFORMERS_OFFLINE": "1",
               "DOCLING_ARTIFACTS_OFFLINE": "1"}
    child = compose_child_env(parent, overlay)
    assert child["HF_HUB_OFFLINE"] == "1"
    assert child["TRANSFORMERS_OFFLINE"] == "1"
    assert child["DOCLING_ARTIFACTS_OFFLINE"] == "1"


def test_overlay_overrides_parent_values() -> None:
    """If the parent already has an offline var set to '0' (or any
    other value), the overlay wins. This is the whole point of the
    overlay semantics — the smoke's frozen offline policy is not
    subject to whatever the operator has set in their shell."""
    parent = {"HF_HUB_OFFLINE": "0",  # parent says "online"
              "TRANSFORMERS_OFFLINE": "0",
              "SYSTEMROOT": "C:\\Windows"}
    overlay = {"HF_HUB_OFFLINE": "1",  # overlay forces offline
               "TRANSFORMERS_OFFLINE": "1"}
    child = compose_child_env(parent, overlay)
    assert child["HF_HUB_OFFLINE"] == "1"
    assert child["TRANSFORMERS_OFFLINE"] == "1"
    # And the non-overridden parent key is inherited.
    assert child["SYSTEMROOT"] == "C:\\Windows"


def test_overlay_none_is_pure_inheritance() -> None:
    parent = {"PATH": "a", "USERPROFILE": "b"}
    child = compose_child_env(parent, None)
    assert child == parent
    # Fresh dict, not the same reference.
    assert child is not parent


def test_overlay_empty_is_pure_inheritance() -> None:
    parent = {"PATH": "a"}
    child = compose_child_env(parent, {})
    assert child == parent


def test_overlay_does_not_mutate_parent_environ() -> None:
    """Critical: the harness must never mutate the running process's
    environment. Two calls with the same parent yield identical
    parent state; the child dict is a fresh object."""
    parent = {"PATH": "start-path",
              "SYSTEMROOT": "start-systemroot",
              "HF_HUB_OFFLINE": "0"}
    parent_snapshot = dict(parent)
    _ = compose_child_env(parent, {"HF_HUB_OFFLINE": "1"})
    _ = compose_child_env(parent, {"NEW_KEY": "value"})
    assert parent == parent_snapshot


def test_overlay_returns_new_dict_object_each_call() -> None:
    parent = {"PATH": "a"}
    overlay = {"X": "1"}
    child1 = compose_child_env(parent, overlay)
    child2 = compose_child_env(parent, overlay)
    assert child1 == child2
    assert child1 is not child2  # different objects
    child1["MUTATED"] = "yes"
    assert "MUTATED" not in child2


def test_overlay_essential_windows_vars_survive_alongside_offline() -> None:
    """End-to-end regression matching the exact Attempt #3 pattern.
    The parent env supplies the Windows-critical variables; the
    overlay supplies only the three offline vars. Every Windows
    variable must reach the child intact."""
    parent = {
        "SYSTEMROOT": "C:\\Windows",
        "PATH": "C:\\Windows\\system32;C:\\Windows",
        "TEMP": "C:\\Users\\kalya\\AppData\\Local\\Temp",
        "TMP": "C:\\Users\\kalya\\AppData\\Local\\Temp",
        "USERPROFILE": "C:\\Users\\kalya",
        "APPDATA": "C:\\Users\\kalya\\AppData\\Roaming",
        "LOCALAPPDATA": "C:\\Users\\kalya\\AppData\\Local",
        "COMPUTERNAME": "TEST-BOX",
        "USERNAME": "kalya",
    }
    overlay = {"HF_HUB_OFFLINE": "1",
               "TRANSFORMERS_OFFLINE": "1",
               "DOCLING_ARTIFACTS_OFFLINE": "1"}
    child = compose_child_env(parent, overlay)
    # Every Windows-critical variable is present in the child.
    for k in ("SYSTEMROOT", "PATH", "TEMP", "TMP", "USERPROFILE",
              "APPDATA", "LOCALAPPDATA", "COMPUTERNAME", "USERNAME"):
        assert k in child, f"missing Windows-critical var {k!r} in child env"
    # And the offline overlay is applied.
    for k in overlay:
        assert child[k] == "1"


def test_overlay_uses_real_os_environ_snapshot(monkeypatch) -> None:
    """Regression: the real invoker composes with ``os.environ``, not
    with an empty parent. Verifies the composition sees the real
    process env (mock a value, verify it's inherited)."""
    monkeypatch.setenv("AKSHARAMD_TEST_SENTINEL", "witness")
    child = compose_child_env(os.environ, {"OVERLAY_KEY": "overlaid"})
    assert child["AKSHARAMD_TEST_SENTINEL"] == "witness"
    assert child["OVERLAY_KEY"] == "overlaid"
    # os.environ is not mutated by the composition.
    assert "OVERLAY_KEY" not in os.environ
