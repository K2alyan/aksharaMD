"""Production Windows Firewall backend using PowerShell.

Wires ``FirewallBackend`` (protocol in ``firewall.py``) to Windows
Defender Firewall via ``New-NetFirewallRule`` / ``Get-NetFirewallRule``
/ ``Remove-NetFirewallRule``. Fails closed on any of:

- Ambiguous or unparseable PowerShell output.
- Privilege failure ("access denied" / requires elevation).
- Pre-existing conflicting rule with the smoke's display name.
- Verification mismatch — rule exists but its Direction / Action /
  Program / Enabled binding does not match what the smoke created.
- Cleanup failure — the rule still exists after Remove-NetFirewallRule.

The PowerShell invoker is injectable so tests exercise every branch
without touching the OS.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

# --------------------------------------------------------------------------
# PowerShell invoker protocol.


@dataclass(frozen=True)
class PowerShellResult:
    stdout: str
    stderr: str
    exit_code: int


class PowerShellInvoker(Protocol):
    """Injectable PowerShell driver. Production implementation shells
    out; tests use FakePowerShellInvoker with canned responses."""

    def invoke(self, script: str, timeout_seconds: float) -> PowerShellResult: ...


class RealPowerShellInvoker:
    """Production driver. Held behind an explicit class so synthetic
    tests never accidentally shell out."""

    def invoke(self, script: str, timeout_seconds: float) -> PowerShellResult:  # pragma: no cover
        import subprocess

        # -NoProfile: don't load user profile / PSReadLine.
        # -NonInteractive: refuse prompts (they would hang the smoke).
        # -ExecutionPolicy Bypass: script is inlined; no on-disk script.
        # -Command: single-line script passed as literal.
        argv = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", script,
        ]
        proc = subprocess.run(  # noqa: S603 - powershell path is trusted
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
        return PowerShellResult(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            exit_code=proc.returncode,
        )


# --------------------------------------------------------------------------
# Errors.


class RealFirewallError(RuntimeError):
    """Base error class for RealFirewallBackend."""


class FirewallPrivilegeError(RealFirewallError):
    """PowerShell cmdlet returned an access-denied / requires-elevation
    error. The parent smoke marks this as harness-level."""


class FirewallPreExistingRuleError(RealFirewallError):
    """A rule with the smoke's display name already exists before we
    tried to create it. Refuse to overwrite; the operator must clean
    it up manually. This prevents silently attaching to a rule with
    unknown Program binding."""


class FirewallAmbiguousOutputError(RealFirewallError):
    """PowerShell returned output we cannot parse or verify. Fail
    closed."""


class FirewallVerificationError(RealFirewallError):
    """Rule was created but Get-NetFirewallRule returns a rule whose
    Direction / Action / Program / Enabled does not match what we
    asked for."""


class FirewallCleanupError(RealFirewallError):
    """Rule is still present after Remove-NetFirewallRule."""


# --------------------------------------------------------------------------
# PowerShell script construction — escaping is critical.


_PS_SINGLE_QUOTE_ESCAPE = re.compile(r"'")


def _ps_single_quote(value: str) -> str:
    """Escape a value for a single-quoted PowerShell string literal.

    PowerShell single-quoted strings do not perform interpolation and
    the only character that needs escaping is the single quote itself,
    which is doubled: ``it's`` -> ``'it''s'``. We do NOT accept null
    bytes; presence of one raises so a malicious program path cannot
    smuggle a command boundary in.
    """
    if "\x00" in value:
        raise FirewallAmbiguousOutputError(
            "null byte in value; refusing to construct PowerShell command"
        )
    escaped = _PS_SINGLE_QUOTE_ESCAPE.sub("''", value)
    return f"'{escaped}'"


def build_create_rule_script(*, display_name: str, program_path: str) -> str:
    return (
        f"New-NetFirewallRule "
        f"-DisplayName {_ps_single_quote(display_name)} "
        f"-Direction Outbound "
        f"-Action Block "
        f"-Program {_ps_single_quote(program_path)} "
        f"-Profile Any "
        f"-Enabled True "
        f"| ConvertTo-Json -Depth 3 -Compress"
    )


def build_get_rule_script(*, display_name: str) -> str:
    """Query the rule + resolve its ProgramFilter via
    Get-NetFirewallApplicationFilter so the returned JSON contains
    the Program binding (New-NetFirewallRule's default output
    typically omits it)."""
    escaped_name = _ps_single_quote(display_name)
    return (
        f"$r = Get-NetFirewallRule -DisplayName {escaped_name} -ErrorAction Stop; "
        f"$app = $r | Get-NetFirewallApplicationFilter; "
        f"[PSCustomObject]@{{"
        f"DisplayName=$r.DisplayName; "
        f"Enabled=$r.Enabled.ToString(); "
        f"Direction=$r.Direction.ToString(); "
        f"Action=$r.Action.ToString(); "
        f"Program=$app.Program"
        f"}} | ConvertTo-Json -Depth 3 -Compress"
    )


def build_remove_rule_script(*, display_name: str) -> str:
    return (
        f"Remove-NetFirewallRule "
        f"-DisplayName {_ps_single_quote(display_name)} "
        f"-ErrorAction Stop"
    )


def build_probe_existence_script(*, display_name: str) -> str:
    """Return true/false depending on whether the rule already exists.
    Used before create to detect pre-existing conflicting rules."""
    escaped_name = _ps_single_quote(display_name)
    return (
        f"if (Get-NetFirewallRule -DisplayName {escaped_name} "
        f"-ErrorAction SilentlyContinue) "
        f"{{ 'EXISTS' }} else {{ 'ABSENT' }}"
    )


# --------------------------------------------------------------------------
# Output classification.


_PRIVILEGE_PATTERNS = (
    "requires elevation",
    "access is denied",
    "requesteddenied",
    "unauthorizedaccessexception",
)


def _looks_like_privilege_failure(text: str) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in _PRIVILEGE_PATTERNS)


def _parse_json_object(text: str, *, context: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise FirewallAmbiguousOutputError(
            f"empty PowerShell output for {context}"
        )
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise FirewallAmbiguousOutputError(
            f"unparseable PowerShell JSON output for {context}: "
            f"{stripped[:200]!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise FirewallAmbiguousOutputError(
            f"expected object from {context}, got {type(parsed).__name__}"
        )
    return parsed


# --------------------------------------------------------------------------
# The backend.


@dataclass(frozen=True)
class VerifiedRuleState:
    display_name: str
    enabled: bool
    direction: str
    action: str
    program: str


class RealFirewallBackend:
    """Production ``FirewallBackend`` implementation using
    PowerShell. Fail-closed on every ambiguous or unexpected state.
    """

    def __init__(
        self,
        *,
        invoker: PowerShellInvoker,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._invoker = invoker
        self._timeout = timeout_seconds

    # ------------------------------------------------------------------
    # Public API — matches FirewallBackend Protocol from firewall.py.

    def create_outbound_block_rule(
        self, *, display_name: str, program_path: str,
    ) -> None:
        # 1. Refuse to overwrite a pre-existing rule.
        self._refuse_if_pre_existing(display_name)
        # 2. Create.
        script = build_create_rule_script(
            display_name=display_name, program_path=program_path,
        )
        result = self._invoker.invoke(script, self._timeout)
        self._raise_on_privilege_failure(result, context="New-NetFirewallRule")
        if result.exit_code != 0:
            raise RealFirewallError(
                f"New-NetFirewallRule failed with exit={result.exit_code}: "
                f"{result.stderr[:200]!r}"
            )
        # PowerShell should have printed a rule object; parse it defensively.
        _parse_json_object(result.stdout, context="New-NetFirewallRule")

    def get_rule_state(self, *, display_name: str) -> dict[str, Any]:
        """Return {"display_name", "enabled"} matching the shape the
        ``firewall.FirewallRuleManager`` expects. The verification
        that the rule's Direction / Action / Program actually match
        what we asked for happens in ``verify_rule_bindings``, called
        by ``FirewallRuleManager.create_and_verify``.
        """
        state = self._get_and_parse_state(display_name)
        return {"display_name": state.display_name, "enabled": state.enabled}

    def verify_rule_bindings(
        self,
        *,
        display_name: str,
        expected_program_path: str,
    ) -> VerifiedRuleState:
        """Verify the rule's Direction=Outbound, Action=Block, Enabled,
        Program==expected. Raises FirewallVerificationError on any
        mismatch."""
        state = self._get_and_parse_state(display_name)
        if not state.enabled:
            raise FirewallVerificationError(
                f"rule '{display_name}' is present but disabled"
            )
        if state.direction.lower() != "outbound":
            raise FirewallVerificationError(
                f"rule '{display_name}' Direction={state.direction!r}, "
                f"expected Outbound"
            )
        if state.action.lower() != "block":
            raise FirewallVerificationError(
                f"rule '{display_name}' Action={state.action!r}, expected Block"
            )
        # Program comparison: PowerShell may normalize case / spelling.
        # We compare case-insensitively and require exact match after
        # normalization. Any partial match is refused.
        if state.program.lower() != expected_program_path.lower():
            raise FirewallVerificationError(
                f"rule '{display_name}' Program={state.program!r}, "
                f"expected {expected_program_path!r}"
            )
        return state

    def verify_rule_absent(self, *, display_name: str) -> bool:
        """True iff the rule is not present.

        Uses ``Get-NetFirewallRule ... -ErrorAction SilentlyContinue``
        (via ``build_probe_existence_script``) so the "rule not found"
        PowerShell case does NOT throw; it returns ``ABSENT``. Only
        genuinely ambiguous PowerShell output raises."""
        script = build_probe_existence_script(display_name=display_name)
        result = self._invoker.invoke(script, self._timeout)
        self._raise_on_privilege_failure(result, context="verify_rule_absent probe")
        marker = result.stdout.strip().upper()
        if marker == "ABSENT":
            return True
        if marker == "EXISTS":
            return False
        raise FirewallAmbiguousOutputError(
            f"verify_rule_absent probe returned unexpected marker {marker!r}"
        )

    def remove_rule(self, *, display_name: str) -> None:
        script = build_remove_rule_script(display_name=display_name)
        result = self._invoker.invoke(script, self._timeout)
        self._raise_on_privilege_failure(result, context="Remove-NetFirewallRule")
        if result.exit_code != 0:
            raise FirewallCleanupError(
                f"Remove-NetFirewallRule exit={result.exit_code}: "
                f"{result.stderr[:200]!r}"
            )
        # Verify the rule is actually gone.
        script2 = build_probe_existence_script(display_name=display_name)
        result2 = self._invoker.invoke(script2, self._timeout)
        self._raise_on_privilege_failure(result2, context="probe after remove")
        marker = result2.stdout.strip().upper()
        if marker == "EXISTS":
            raise FirewallCleanupError(
                f"rule '{display_name}' still present after Remove-NetFirewallRule"
            )
        if marker != "ABSENT":
            raise FirewallAmbiguousOutputError(
                f"post-remove probe returned unexpected marker {marker!r}"
            )

    # ------------------------------------------------------------------
    # Internals.

    def _refuse_if_pre_existing(self, display_name: str) -> None:
        script = build_probe_existence_script(display_name=display_name)
        result = self._invoker.invoke(script, self._timeout)
        self._raise_on_privilege_failure(result, context="pre-existence probe")
        marker = result.stdout.strip().upper()
        if marker == "EXISTS":
            raise FirewallPreExistingRuleError(
                f"rule '{display_name}' already exists before smoke start; "
                f"refusing to overwrite. Clean up the stale rule manually "
                f"before re-running the smoke."
            )
        if marker != "ABSENT":
            raise FirewallAmbiguousOutputError(
                f"pre-existence probe returned unexpected marker {marker!r}"
            )

    def _get_and_parse_state(self, display_name: str) -> VerifiedRuleState:
        script = build_get_rule_script(display_name=display_name)
        result = self._invoker.invoke(script, self._timeout)
        self._raise_on_privilege_failure(result, context="Get-NetFirewallRule")
        if result.exit_code != 0:
            raise RealFirewallError(
                f"Get-NetFirewallRule exit={result.exit_code}: "
                f"{result.stderr[:200]!r}"
            )
        obj = _parse_json_object(result.stdout, context="Get-NetFirewallRule")
        try:
            return VerifiedRuleState(
                display_name=str(obj["DisplayName"]),
                enabled=str(obj["Enabled"]).lower() == "true",
                direction=str(obj["Direction"]),
                action=str(obj["Action"]),
                program=str(obj["Program"] or ""),
            )
        except KeyError as exc:
            raise FirewallAmbiguousOutputError(
                f"Get-NetFirewallRule JSON missing field: {exc}"
            ) from exc

    def _raise_on_privilege_failure(
        self, result: PowerShellResult, *, context: str,
    ) -> None:
        combined = f"{result.stdout}\n{result.stderr}"
        if _looks_like_privilege_failure(combined):
            raise FirewallPrivilegeError(
                f"{context} failed with a privilege / access-denied error. "
                f"Real firewall changes require an elevated PowerShell "
                f"session. Stderr: {result.stderr[:200]!r}"
            )
