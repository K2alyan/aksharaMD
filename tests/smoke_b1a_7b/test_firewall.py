"""Firewall probe classification + rule lifecycle."""
from __future__ import annotations

import errno

import pytest

from benchmarks.eval_v1.smoke_b1a_7b import firewall as fw
from tests.smoke_b1a_7b.fakes import (
    BlockedProbeBackend,
    DnsFailureProbeBackend,
    FakeFirewallBackend,
    OpenProbeBackend,
)

# ---------------------------------------------------------------------------
# probe_is_admissible_block classification.


def test_classification_connected_is_not_blocked() -> None:
    pr = fw.ProbeResult(name="x", connected=True, exc_class_name=None, errno_int=None, detail="")
    assert fw.probe_is_admissible_block(pr) is False


def test_classification_connection_refused_is_blocked() -> None:
    pr = fw.ProbeResult(name="x", connected=False,
                         exc_class_name="ConnectionRefusedError",
                         errno_int=errno.ECONNREFUSED, detail="")
    assert fw.probe_is_admissible_block(pr) is True


def test_classification_timeout_is_blocked() -> None:
    pr = fw.ProbeResult(name="x", connected=False, exc_class_name="TimeoutError",
                         errno_int=None, detail="")
    assert fw.probe_is_admissible_block(pr) is True


def test_classification_oserror_with_expected_errno_is_blocked() -> None:
    for e in (errno.ETIMEDOUT, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EACCES):
        pr = fw.ProbeResult(name="x", connected=False, exc_class_name="OSError",
                             errno_int=e, detail="")
        assert fw.probe_is_admissible_block(pr) is True, e


def test_classification_oserror_with_bogus_errno_is_not_blocked() -> None:
    pr = fw.ProbeResult(name="x", connected=False, exc_class_name="OSError",
                         errno_int=999999, detail="")
    assert fw.probe_is_admissible_block(pr) is False


def test_classification_dns_failure_is_not_blocked() -> None:
    pr = fw.ProbeResult(name="x", connected=False, exc_class_name="gaierror",
                         errno_int=None, detail="")
    assert fw.probe_is_admissible_block(pr) is False


def test_classification_unknown_exception_is_not_blocked() -> None:
    pr = fw.ProbeResult(name="x", connected=False, exc_class_name="RuntimeError",
                         errno_int=None, detail="")
    assert fw.probe_is_admissible_block(pr) is False


# ---------------------------------------------------------------------------
# run_probes composition.


def test_run_probes_blocked_backend_yields_blocked_true() -> None:
    obs = fw.run_probes(BlockedProbeBackend())
    assert obs.blocked is True


def test_run_probes_open_backend_yields_blocked_false() -> None:
    obs = fw.run_probes(OpenProbeBackend())
    assert obs.blocked is False


def test_run_probes_dns_failure_yields_blocked_false() -> None:
    obs = fw.run_probes(DnsFailureProbeBackend())
    assert obs.blocked is False


# ---------------------------------------------------------------------------
# Firewall rule lifecycle.


def test_firewall_create_and_verify_happy_path() -> None:
    backend = FakeFirewallBackend()
    mgr = fw.FirewallRuleManager(backend)
    state = mgr.create_and_verify(program_path="/fake/parser_worker.exe")
    assert state.enabled is True
    assert state.display_name == fw.FIREWALL_RULE_DISPLAY_NAME
    assert ("create", {"display_name": fw.FIREWALL_RULE_DISPLAY_NAME,
                       "program_path": "/fake/parser_worker.exe"}) in backend.calls


def test_firewall_create_but_not_enabled_raises() -> None:
    backend = FakeFirewallBackend()
    # Simulate the OS reporting the rule is not enabled after create.
    original_create = backend.create_outbound_block_rule

    def _create(*, display_name, program_path):
        original_create(display_name=display_name, program_path=program_path)
        backend.rule_present = False

    backend.create_outbound_block_rule = _create  # type: ignore[assignment]
    mgr = fw.FirewallRuleManager(backend)
    with pytest.raises(fw.FirewallRuleError, match="not enabled"):
        mgr.create_and_verify(program_path="/fake")


def test_firewall_cleanup_happy_path() -> None:
    backend = FakeFirewallBackend()
    backend.rule_present = True
    mgr = fw.FirewallRuleManager(backend)
    mgr.cleanup()
    assert backend.rule_present is False


def test_firewall_cleanup_linger_raises() -> None:
    backend = FakeFirewallBackend()
    backend.rule_present = True
    backend.linger_after_remove = True  # remove is called, but rule stays
    mgr = fw.FirewallRuleManager(backend)
    with pytest.raises(fw.FirewallRuleError, match="still enabled"):
        mgr.cleanup()
