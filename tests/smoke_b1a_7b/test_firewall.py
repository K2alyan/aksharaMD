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
# HTTPS probe exception classification.
#
# B1a-7b.2c real-smoke observation: on Attempt #1, the HTTPS probe against
# ``https://1.1.1.1/`` failed with ``ssl.SSLCertVerificationError``
# because Python's default CA bundle on this host does not trust the
# certificate chain presented by that endpoint. The TCP+TLS handshake
# reached cert-verification — evidence that egress works. The previous
# classifier incorrectly reported ``connected=False`` (via the OSError
# catch-all, since SSLError inherits from OSError), causing the
# pre-smoke positive control to fail and the smoke to return
# INCONCLUSIVE without executing any parser.
#
# These tests pin the corrected behavior. They do NOT weaken the
# blocked-egress invariant: firewall-blocked cases fail at the TCP
# layer before TLS starts, so no ``ssl.SSLError`` can arise from a
# genuine block.


def test_ssl_cert_verification_error_maps_to_connected_true() -> None:
    """The exact exception observed in Attempt #1: cert-verify error
    means the TLS handshake reached the remote endpoint. Egress works."""
    import ssl
    exc = ssl.SSLCertVerificationError(
        "certificate verify failed: unable to get local issuer certificate",
    )
    result = fw._https_result_from_exception(exc)
    assert result.connected is True
    assert result.exc_class_name == "SSLCertVerificationError"
    assert "tls handshake reached endpoint" in result.detail.lower()


def test_ssl_error_generic_maps_to_connected_true() -> None:
    """Any TLS-layer error — bad protocol version, cipher mismatch,
    truncated record — indicates the TLS handshake started, which in
    turn indicates the TCP connection succeeded. Egress reachable."""
    import ssl
    exc = ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
    result = fw._https_result_from_exception(exc)
    assert result.connected is True
    assert result.exc_class_name == "SSLError"


def test_https_probe_connection_refused_still_classified_as_block() -> None:
    """The invariant: under a real firewall block, TCP fails BEFORE
    TLS starts. This test proves that ConnectionRefusedError (typical
    blocked-egress outcome) is still classified correctly."""
    import errno as _errno
    exc = ConnectionRefusedError(_errno.ECONNREFUSED, "refused")
    result = fw._https_result_from_exception(exc)
    assert result.connected is False
    assert result.exc_class_name == "ConnectionRefusedError"
    assert fw.probe_is_admissible_block(result) is True


def test_https_probe_timeout_still_classified_as_block() -> None:
    """Under a DROP-style firewall, the connect attempt times out.
    That case must still be classified as a block."""
    exc = TimeoutError("timed out")
    result = fw._https_result_from_exception(exc)
    assert result.connected is False
    assert result.exc_class_name == "TimeoutError"
    assert fw.probe_is_admissible_block(result) is True


def test_https_probe_oserror_with_net_unreach_still_classified_as_block() -> None:
    import errno as _errno
    exc = OSError(_errno.ENETUNREACH, "network unreachable")
    result = fw._https_result_from_exception(exc)
    assert result.connected is False
    assert result.exc_class_name == "OSError"
    assert result.errno_int == _errno.ENETUNREACH
    assert fw.probe_is_admissible_block(result) is True


def test_https_probe_dns_failure_still_not_admissible_and_not_reachable() -> None:
    """DNS failure alone is neither admissible-block nor evidence of
    reachability. It's just unusable evidence (bare-IP probe should
    not gaierror; if it does, refuse to certify anything)."""
    import socket as _socket
    exc = _socket.gaierror("host lookup failed")
    result = fw._https_result_from_exception(exc)
    assert result.connected is False
    assert result.exc_class_name == "gaierror"
    assert fw.probe_is_admissible_block(result) is False


def test_admissible_block_classification_treats_ssl_cert_result_as_not_blocked() -> None:
    """Sanity: after the classifier change, feeding an SSL-cert
    ProbeResult through probe_is_admissible_block returns False —
    meaning 'not admissible as evidence of block' — because
    connected=True (egress reachable)."""
    import ssl
    exc = ssl.SSLCertVerificationError("unable to get local issuer certificate")
    result = fw._https_result_from_exception(exc)
    assert fw.probe_is_admissible_block(result) is False


def test_positive_control_passes_when_https_hits_ssl_cert_error() -> None:
    """End-to-end regression against the exact Attempt #1 failure.
    A probe backend where TCP connects and HTTPS raises
    SSLCertVerificationError should be treated by ``run_probes`` as
    both probes reachable — i.e., the pre-smoke positive control
    would pass and the smoke proceeds to firewall setup."""
    import ssl

    class _AttemptOneReplayBackend:
        """Reproduces the exact classifier inputs from the observed
        Attempt #1 failure: TCP connect succeeds, HTTPS raises
        SSLCertVerificationError."""

        def tcp_connect(self, host, port, timeout):
            return fw.ProbeResult(
                name="tcp_connect", connected=True,
                exc_class_name=None, errno_int=None,
                detail="tcp connected (attempt-1 replay)",
            )

        def https_get(self, url, timeout):
            exc = ssl.SSLCertVerificationError(
                "certificate verify failed: unable to get local issuer certificate",
            )
            return fw._https_result_from_exception(exc)

    obs = fw.run_probes(_AttemptOneReplayBackend())
    # Both probes reached the endpoint → both connected=True.
    assert obs.probe_tcp_connect.connected is True
    assert obs.probe_https_get.connected is True
    # blocked = admissible_block on BOTH; TCP is connected → False,
    # HTTPS is connected → False → blocked overall = False.
    assert obs.blocked is False

    # And critically: the positive-control classifier (used by
    # smoke_runner._pc_pass) requires both connected to be True.
    # That's how we verify the fix repairs the Attempt #1 failure.
    both_reached = (obs.probe_tcp_connect.connected
                    and obs.probe_https_get.connected)
    assert both_reached is True


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
    with pytest.raises(fw.FirewallRuleError, match="still present after cleanup"):
        mgr.cleanup()


# ---------------------------------------------------------------------------
# verify_rule_absent — explicit yes/no query replacing the previous
# exception-based absence detection. Attempt #3 of the real smoke
# crashed at cleanup because the previous exception-based approach
# relied on FirewallRuleError being raised by RealFirewallBackend.
# get_rule_state on rule-absent — but RealFirewallBackend actually
# raised its own RealFirewallError, a different class.


def test_verify_rule_absent_true_when_rule_not_present() -> None:
    backend = FakeFirewallBackend()
    backend.rule_present = False
    assert backend.verify_rule_absent(display_name="X") is True


def test_verify_rule_absent_false_when_rule_present() -> None:
    backend = FakeFirewallBackend()
    backend.rule_present = True
    assert backend.verify_rule_absent(display_name="X") is False


def test_cleanup_uses_verify_rule_absent_and_completes_cleanly() -> None:
    """Regression for Attempt #3: after a successful Remove, cleanup
    must complete cleanly. It queries verify_rule_absent explicitly
    rather than catching FirewallRuleError from a get_rule_state
    call whose exception class is not guaranteed by the Protocol."""
    backend = FakeFirewallBackend()
    backend.rule_present = True
    mgr = fw.FirewallRuleManager(backend)
    mgr.cleanup()  # no raise

    call_names = [c[0] for c in backend.calls]
    # remove_rule was called; verify_rule_absent was called; get_rule_state
    # is NOT called on the cleanup path anymore.
    assert "remove" in call_names
    assert "verify_absent" in call_names
    assert "get" not in call_names
    # And the rule is verifiably gone.
    assert backend.rule_present is False


def test_cleanup_call_order_is_remove_then_verify() -> None:
    """Sanity: the harness never verifies absence before actually
    removing. That order is what makes the query meaningful."""
    backend = FakeFirewallBackend()
    backend.rule_present = True
    mgr = fw.FirewallRuleManager(backend)
    mgr.cleanup()
    kinds_after_verify_start = [c[0] for c in backend.calls]
    # First cleanup call is remove, then verify_absent.
    assert kinds_after_verify_start[0] == "remove"
    assert kinds_after_verify_start[1] == "verify_absent"
