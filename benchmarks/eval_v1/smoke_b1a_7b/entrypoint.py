"""Explicit CLI entry point for the eventual 12-run smoke execution.

This module intentionally does NOT execute anything on import. Real
smoke execution requires calling ``main()`` from a top-level script
that has explicit authorization. The B1a-7b.2a implementation ships
this entry point but does not invoke it; the 12-run smoke (B1a-7b.2b)
is a separate authorization step.

Even ``main()`` refuses to run unless the ``AKSHARAMD_SMOKE_AUTHORIZED``
environment variable is set to the exact string
``"b1a_7b_2b_authorized"``. This is a coarse authorization sentinel,
not a security control: it prevents accidental invocation from tests
or documentation examples. The real authorization is the reviewer's
explicit ok in-conversation.
"""
from __future__ import annotations

import os
import sys

AUTHORIZATION_ENV_VAR = "AKSHARAMD_SMOKE_AUTHORIZED"
AUTHORIZATION_TOKEN = "b1a_7b_2b_authorized"


class UnauthorizedInvocationError(RuntimeError):
    """The entry point was invoked without the explicit authorization
    sentinel. Refuses to proceed."""


def _check_authorization() -> None:
    token = os.environ.get(AUTHORIZATION_ENV_VAR, "")
    if token != AUTHORIZATION_TOKEN:
        raise UnauthorizedInvocationError(
            f"Entry point requires {AUTHORIZATION_ENV_VAR}={AUTHORIZATION_TOKEN!r}. "
            f"The 12-run smoke is a separate authorization step (B1a-7b.2b) and "
            f"was not authorized by the B1a-7b.2a harness-implementation PR. "
            f"If you are seeing this error, do not set the token; instead, "
            f"return to the review conversation and request explicit authorization."
        )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    """Real execution entry point. Not covered by the synthetic tests
    because doing so would require setting the authorization sentinel
    and wiring real backends; the smoke's *runner* is tested end-to-end
    against fakes via ``smoke_runner.SmokeRunner`` directly.

    B1a-7b.2b will introduce the real-backend wiring here.
    """
    _check_authorization()
    print(
        "B1a-7b.2b real-backend wiring is not implemented in this PR.",
        file=sys.stderr,
    )
    print(
        "This entry point exists so the CLI shape is fixed; B1a-7b.2b "
        "will implement RealProbeBackend / real firewall backend / real "
        "adapter binding here.",
        file=sys.stderr,
    )
    return 2  # not-implemented distinct from run failure


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
