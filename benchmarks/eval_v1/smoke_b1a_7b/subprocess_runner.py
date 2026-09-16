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
    argv: list[str]
    timeout_seconds: float
    env: Mapping[str, str] | None = None
    stdin_bytes: bytes = b""
    cwd: str | None = None
    # Fields carried through to the result but not used by the runner.
    context: dict[str, Any] = field(default_factory=dict)


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
        env = dict(os.environ if invocation.env is None else invocation.env)
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
