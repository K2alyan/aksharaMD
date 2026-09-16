"""RealFirewallBackend PowerShell construction + branch coverage.

No real PowerShell invocation happens. FakePowerShellInvoker returns
canned stdout/stderr/exit_code so every branch of the backend is
exercised.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from benchmarks.eval_v1.smoke_b1a_7b import real_firewall as rf

# ---------------------------------------------------------------------------
# Fake PowerShell invoker.


@dataclass
class FakePowerShellInvoker:
    """Canned responses. ``responses`` is a list of (script_matcher,
    result) tuples; the first matching response is returned. If no
    match, raises. All calls are recorded in ``calls``."""

    responses: list[tuple[str, rf.PowerShellResult]] = field(default_factory=list)
    calls: list[tuple[str, float]] = field(default_factory=list)

    def invoke(self, script: str, timeout_seconds: float) -> rf.PowerShellResult:
        self.calls.append((script, timeout_seconds))
        for needle, result in self.responses:
            if needle in script:
                return result
        raise AssertionError(
            f"FakePowerShellInvoker received unmatched script: "
            f"{script[:200]!r}"
        )


def _ok(stdout: str = "", stderr: str = "") -> rf.PowerShellResult:
    return rf.PowerShellResult(stdout=stdout, stderr=stderr, exit_code=0)


def _err(stderr: str, exit_code: int = 1) -> rf.PowerShellResult:
    return rf.PowerShellResult(stdout="", stderr=stderr, exit_code=exit_code)


# ---------------------------------------------------------------------------
# PowerShell script construction — escaping.


def test_ps_single_quote_no_special_chars() -> None:
    assert rf._ps_single_quote("hello") == "'hello'"


def test_ps_single_quote_escapes_apostrophes() -> None:
    assert rf._ps_single_quote("it's") == "'it''s'"


def test_ps_single_quote_paths_with_spaces() -> None:
    p = r"C:\Program Files\App\worker.exe"
    assert rf._ps_single_quote(p) == f"'{p}'"


def test_ps_single_quote_refuses_null_byte() -> None:
    with pytest.raises(rf.FirewallAmbiguousOutputError, match="null byte"):
        rf._ps_single_quote("evil\x00smuggle")


def test_create_rule_script_contains_all_required_flags() -> None:
    script = rf.build_create_rule_script(
        display_name="AksharaMD-Smoke-Egress-Block",
        program_path=r"C:\bin\worker.exe",
    )
    assert "New-NetFirewallRule" in script
    assert "-DisplayName 'AksharaMD-Smoke-Egress-Block'" in script
    assert "-Direction Outbound" in script
    assert "-Action Block" in script
    assert r"-Program 'C:\bin\worker.exe'" in script
    assert "-Profile Any" in script
    assert "-Enabled True" in script
    assert "ConvertTo-Json" in script


def test_get_rule_script_resolves_program_filter() -> None:
    script = rf.build_get_rule_script(display_name="AksharaMD-Smoke-Egress-Block")
    assert "Get-NetFirewallRule" in script
    assert "Get-NetFirewallApplicationFilter" in script
    assert "Program=" in script
    assert "ConvertTo-Json" in script


def test_remove_rule_script_uses_error_action_stop() -> None:
    script = rf.build_remove_rule_script(display_name="X")
    assert "Remove-NetFirewallRule" in script
    assert "-ErrorAction Stop" in script


def test_probe_existence_script_prints_exists_or_absent() -> None:
    script = rf.build_probe_existence_script(display_name="X")
    assert "'EXISTS'" in script
    assert "'ABSENT'" in script


# ---------------------------------------------------------------------------
# create_outbound_block_rule


def test_create_happy_path_after_absent_probe() -> None:
    rule_json = json.dumps({"DisplayName": "X", "Enabled": "True"})
    invoker = FakePowerShellInvoker(responses=[
        ("if (Get-NetFirewallRule", _ok(stdout="ABSENT")),
        ("New-NetFirewallRule", _ok(stdout=rule_json)),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    backend.create_outbound_block_rule(
        display_name="AksharaMD-Smoke-Egress-Block",
        program_path=r"C:\bin\worker.exe",
    )
    # First call was the probe; second was New.
    assert len(invoker.calls) == 2
    assert "New-NetFirewallRule" in invoker.calls[1][0]


def test_create_refuses_pre_existing_rule() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("if (Get-NetFirewallRule", _ok(stdout="EXISTS")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallPreExistingRuleError, match="already exists"):
        backend.create_outbound_block_rule(
            display_name="AksharaMD-Smoke-Egress-Block",
            program_path=r"C:\bin\worker.exe",
        )


def test_create_privilege_failure_raises() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("if (Get-NetFirewallRule", _ok(stdout="ABSENT")),
        ("New-NetFirewallRule",
         _err(stderr="Access is denied. Requires elevation.", exit_code=1)),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallPrivilegeError, match="privilege"):
        backend.create_outbound_block_rule(
            display_name="AksharaMD-Smoke-Egress-Block",
            program_path=r"C:\bin\worker.exe",
        )


def test_create_ambiguous_output_raises() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("if (Get-NetFirewallRule", _ok(stdout="MAYBE")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallAmbiguousOutputError, match="MAYBE"):
        backend.create_outbound_block_rule(
            display_name="X", program_path="/x",
        )


def test_create_unparseable_new_output_raises() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("if (Get-NetFirewallRule", _ok(stdout="ABSENT")),
        ("New-NetFirewallRule", _ok(stdout="this is not JSON")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallAmbiguousOutputError, match="unparseable"):
        backend.create_outbound_block_rule(display_name="X", program_path="/x")


# ---------------------------------------------------------------------------
# verify_rule_bindings


def _get_response(**overrides: Any) -> rf.PowerShellResult:
    base = {
        "DisplayName": "AksharaMD-Smoke-Egress-Block",
        "Enabled": "True",
        "Direction": "Outbound",
        "Action": "Block",
        "Program": r"C:\bin\worker.exe",
    }
    base.update(overrides)
    return _ok(stdout=json.dumps(base))


def test_verify_bindings_happy_path() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Get-NetFirewallRule", _get_response()),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    state = backend.verify_rule_bindings(
        display_name="AksharaMD-Smoke-Egress-Block",
        expected_program_path=r"C:\bin\worker.exe",
    )
    assert state.enabled is True
    assert state.direction == "Outbound"
    assert state.action == "Block"
    assert state.program.lower() == r"C:\bin\worker.exe".lower()


def test_verify_bindings_direction_mismatch_raises() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Get-NetFirewallRule", _get_response(Direction="Inbound")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallVerificationError, match="Direction"):
        backend.verify_rule_bindings(
            display_name="X", expected_program_path=r"C:\bin\worker.exe",
        )


def test_verify_bindings_action_mismatch_raises() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Get-NetFirewallRule", _get_response(Action="Allow")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallVerificationError, match="Action"):
        backend.verify_rule_bindings(
            display_name="X", expected_program_path=r"C:\bin\worker.exe",
        )


def test_verify_bindings_program_mismatch_raises() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Get-NetFirewallRule", _get_response(Program=r"C:\other\path.exe")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallVerificationError, match="Program"):
        backend.verify_rule_bindings(
            display_name="X", expected_program_path=r"C:\bin\worker.exe",
        )


def test_verify_bindings_disabled_raises() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Get-NetFirewallRule", _get_response(Enabled="False")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallVerificationError, match="disabled"):
        backend.verify_rule_bindings(
            display_name="X", expected_program_path=r"C:\bin\worker.exe",
        )


def test_verify_bindings_missing_field_raises() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Get-NetFirewallRule",
         _ok(stdout=json.dumps({"DisplayName": "X", "Enabled": "True"}))),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallAmbiguousOutputError, match="missing field"):
        backend.verify_rule_bindings(display_name="X", expected_program_path="/x")


# ---------------------------------------------------------------------------
# get_rule_state (FirewallBackend Protocol shape)


def test_get_rule_state_returns_display_name_and_enabled() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Get-NetFirewallRule", _get_response()),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    state = backend.get_rule_state(display_name="AksharaMD-Smoke-Egress-Block")
    assert state == {"display_name": "AksharaMD-Smoke-Egress-Block", "enabled": True}


# ---------------------------------------------------------------------------
# remove_rule


def test_remove_happy_path_then_absent_probe() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Remove-NetFirewallRule", _ok()),
        ("if (Get-NetFirewallRule", _ok(stdout="ABSENT")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    backend.remove_rule(display_name="X")
    assert len(invoker.calls) == 2


def test_remove_but_rule_lingers_raises_cleanup_error() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Remove-NetFirewallRule", _ok()),
        ("if (Get-NetFirewallRule", _ok(stdout="EXISTS")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallCleanupError, match="still present"):
        backend.remove_rule(display_name="X")


def test_remove_exit_nonzero_raises_cleanup_error() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Remove-NetFirewallRule", _err(stderr="something broke", exit_code=1)),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallCleanupError, match="exit=1"):
        backend.remove_rule(display_name="X")


def test_remove_privilege_failure_raises_privilege_error() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Remove-NetFirewallRule",
         _err(stderr="Access is denied.", exit_code=1)),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallPrivilegeError, match="privilege"):
        backend.remove_rule(display_name="X")


def test_verify_rule_absent_true_on_absent_marker() -> None:
    """The successful cleanup path from Attempt #3: after remove, the
    EXISTS/ABSENT probe (using -ErrorAction SilentlyContinue) returns
    ABSENT. verify_rule_absent must return True — cleanup succeeds."""
    invoker = FakePowerShellInvoker(responses=[
        ("if (Get-NetFirewallRule", _ok(stdout="ABSENT")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    assert backend.verify_rule_absent(display_name="X") is True


def test_verify_rule_absent_false_on_exists_marker() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("if (Get-NetFirewallRule", _ok(stdout="EXISTS")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    assert backend.verify_rule_absent(display_name="X") is False


def test_verify_rule_absent_raises_on_ambiguous_marker() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("if (Get-NetFirewallRule", _ok(stdout="WHO KNOWS")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallAmbiguousOutputError, match="WHO KNOWS"):
        backend.verify_rule_absent(display_name="X")


def test_verify_rule_absent_raises_on_privilege_failure() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("if (Get-NetFirewallRule",
         _err(stderr="Access is denied.", exit_code=1)),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallPrivilegeError, match="privilege"):
        backend.verify_rule_absent(display_name="X")


def test_remove_post_probe_ambiguous_raises() -> None:
    invoker = FakePowerShellInvoker(responses=[
        ("Remove-NetFirewallRule", _ok()),
        ("if (Get-NetFirewallRule", _ok(stdout="WHO KNOWS")),
    ])
    backend = rf.RealFirewallBackend(invoker=invoker)
    with pytest.raises(rf.FirewallAmbiguousOutputError, match="WHO KNOWS"):
        backend.remove_rule(display_name="X")
