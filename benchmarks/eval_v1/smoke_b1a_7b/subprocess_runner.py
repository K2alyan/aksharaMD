"""Subprocess invocation for parser workers.

All four parser adapters launch their parser in a fresh child process
per (document, parser) invocation. The parent harness enforces the
per-parser timeout from the parser-execution contract §4 and — on
expiry — kills the entire child process tree so any GPU worker or
subprocess spawned by the parser is reclaimed.

The Windows implementation uses ``subprocess.Popen`` with
``CREATE_NEW_PROCESS_GROUP`` so a process-tree kill can target the
whole group. Tree kill is performed via
``taskkill /T /F /PID <pid>`` on Windows; on POSIX we would use
``os.killpg`` with SIGKILL, but production is Windows-primary.

Environment semantics (B1a-7b.2d correction): ``SubprocessInvocation.env``
is an *overlay* — its keys override the parent's ``os.environ``, but
all other parent environment variables are inherited. This is
critical on Windows, where launching Python without ``SYSTEMROOT`` /
``PATH`` / ``TEMP`` / ``USERPROFILE`` causes ``OSError`` before the
child does any real work. The previous "env=None means inherit, env=X
means replace entirely" behaviour caused Attempt #3 to emit 12
uniform ``<parser>_exception:OSError`` DEFECTs in ~0.2s.

The tests inject ``FakeSubprocessInvoker`` (in tests/smoke_b1a_7b/
fakes.py) and never spawn a real child process.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SubprocessInvocation:
    """One subprocess invocation request.

    ``env`` is an OVERLAY — the child receives ``os.environ`` merged
    with these entries, with ``env`` taking precedence. On Windows,
    replacing the environment entirely would strip ``SYSTEMROOT`` /
    ``PATH`` / ``TEMP`` / ``USERPROFILE`` and cause the child to
    ``OSError`` before it does any real work.
    """

    argv: list[str]
    timeout_seconds: float
    env: Mapping[str, str] | None = None
    stdin_bytes: bytes = b""
    cwd: str | None = None
    # Fields carried through to the result but not used by the runner.
    context: dict[str, Any] = field(default_factory=dict)


def compose_child_env(
    parent_env: Mapping[str, str],
    overlay: Mapping[str, str] | None,
) -> dict[str, str]:
    """Compose the child process's environment.

    Returns a new dict = ``dict(parent_env)`` with ``overlay`` (if
    any) applied on top. The parent mapping is never mutated. Overlay
    keys override parent keys of the same name.

    Extracted as a pure function so tests can drive every branch
    without spawning a real subprocess (``RealSubprocessInvoker`` is
    ``# pragma: no cover``).
    """
    child = dict(parent_env)
    if overlay is not None:
        child.update(overlay)
    return child


@dataclass(frozen=True)
class SubprocessResult:
    stdout: bytes
    stderr: bytes
    exit_code: int
    wall_clock_seconds: float
    timed_out: bool


class SubprocessInvoker(Protocol):
    """Injectable subprocess driver."""

    def invoke(self, invocation: SubprocessInvocation) -> SubprocessResult: ...


# --------------------------------------------------------------------------
# Real implementation. Held behind an explicit class; conftest guards
# refuse this class instantiation during synthetic tests.


class RealSubprocessInvoker:
    """Production driver. Launches a child process with a fresh
    process group / job on Windows and enforces the timeout with a
    tree kill on expiry."""

    def invoke(self, invocation: SubprocessInvocation) -> SubprocessResult:  # pragma: no cover
        env = compose_child_env(os.environ, invocation.env)
        creationflags = 0
        if sys.platform == "win32":
            # Fresh process group so taskkill /T /F reaches every child.
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        started = time.monotonic()
        # noqa: S603 - argv is trusted (built by the harness, not user input).
        proc = subprocess.Popen(
            invocation.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=invocation.cwd,
            shell=False,
            creationflags=creationflags,
        )
        try:
            stdout_b, stderr_b = proc.communicate(
                input=invocation.stdin_bytes,
                timeout=invocation.timeout_seconds,
            )
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_process_tree(proc.pid)
            try:
                stdout_b, stderr_b = proc.communicate(timeout=5.0)
            except subprocess.TimeoutExpired:
                stdout_b, stderr_b = b"", b""
        elapsed = time.monotonic() - started
        return SubprocessResult(
            stdout=stdout_b or b"",
            stderr=stderr_b or b"",
            exit_code=proc.returncode if proc.returncode is not None else -1,
            wall_clock_seconds=elapsed,
            timed_out=timed_out,
        )

    @staticmethod
    def _kill_process_tree(pid: int) -> None:  # pragma: no cover
        if sys.platform == "win32":
            # taskkill /T = tree, /F = force, /PID targets the pid.
            subprocess.run(  # noqa: S603 - taskkill path is trusted
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                shell=False,
                timeout=5.0,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), 9)  # SIGKILL
            except OSError:
                pass
