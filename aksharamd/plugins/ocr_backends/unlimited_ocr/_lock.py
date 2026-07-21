"""Filesystem lock for Unlimited-OCR model lifecycle operations (PR 98).

The ``install`` and ``remove`` commands mutate the on-disk model
snapshot and the aksharamd verification receipt. Concurrent runs of
either command (from two shells, or from a user shell plus a CI job)
can corrupt the snapshot or leave the receipt out of sync. This
module provides an advisory filesystem lock keyed by lock-file path
that both commands acquire before touching anything.

## Contract

Use as a context manager:

.. code-block:: python

    with model_lock(lock_path, operation="install", stale_seconds=900):
        ...  # atomic install / remove work

Acquisition is atomic across processes on POSIX and Windows via
``os.open(..., O_CREAT | O_EXCL | O_WRONLY)`` — the OS returns
``FileExistsError`` if the lock file already exists.

The lock file contains a small JSON payload:

.. code-block:: json

    {
      "pid": 12345,
      "hostname": "myhost",
      "created_at": "2026-07-21T14:23:11Z",
      "operation": "install"
    }

## Stale-lock detection

If ``LockHeld`` would be raised, the current lock is examined:

1. If its ``created_at`` is older than ``stale_seconds`` (default
   900s / 15 min): the lock is broken and re-acquired. The threshold
   is deliberately conservative — install downloads can take 30+ min
   on slow networks, so a shorter threshold would race.
2. If the file records the SAME hostname and the recorded ``pid`` is
   no longer alive (POSIX ``os.kill(pid, 0)``; Windows
   ``OpenProcess``): the lock is broken.
3. If the lock file is malformed (bad JSON, missing keys, wrong
   types): treat as ACTIVE. This is the conservative choice — a
   half-written lock file might belong to a currently running
   install and we would rather force the user to run
   ``aksharamd models remove`` (which will remove the stale lock via
   the age heuristic) than corrupt an in-flight download.

## Scope

The lock protects ``install`` and ``remove`` only. ``status`` and
``verify`` are read-only and do not acquire it.

## Filesystem caveats

On networked filesystems (NFS without close-to-open cache flushes,
SMB with lazy metadata, etc.) the atomicity of
``O_CREAT | O_EXCL | O_WRONLY`` degrades. This lock is intended for
the local HuggingFace cache directory only. If the cache lives on a
network share, users should serialize install/remove manually and
not rely on this lock.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import platform
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)

# Default stale-lock threshold. 15 minutes is a compromise: long
# enough that a slow download does not race; short enough that a
# genuinely orphaned lock does not block the user for hours.
_DEFAULT_STALE_SECONDS = 900

# Set of operations we allow to acquire the lock. Keeps typos out of
# the recorded metadata.
_ALLOWED_OPERATIONS = frozenset({"install", "remove"})


class LockHeld(Exception):
    """Raised when the model lock is held by another process."""

    def __init__(
        self,
        lock_path: Path,
        holder: dict | None,
        message: str,
    ) -> None:
        super().__init__(message)
        self.lock_path = lock_path
        self.holder = holder


@dataclass(frozen=True)
class LockMetadata:
    """Parsed contents of a lock file."""

    pid: int
    hostname: str
    created_at: str  # ISO 8601 UTC (e.g., "2026-07-21T14:23:11Z")
    operation: str


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_iso_utc(text: str) -> float | None:
    """Return a POSIX timestamp for ``text`` or None on parse failure.

    Accepts the exact format written by :func:`_now_iso_utc`.
    """
    try:
        struct = time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    # ``strptime`` treats the parsed value as local time; convert to
    # UTC epoch by using ``calendar.timegm`` so daylight-savings
    # transitions do not shift the recorded time.
    import calendar
    return calendar.timegm(struct)


def _is_pid_alive(pid: int) -> bool:
    """Best-effort probe: does ``pid`` refer to a running process?

    Returns True on any indication of "yes" (including permission
    denied — the PID exists, we just cannot signal it) and False only
    when the OS confirms the PID is not in the process table.
    """
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        return _is_pid_alive_windows(pid)
    return _is_pid_alive_posix(pid)


def _is_pid_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists, we just do not have permission to signal it.
        return True
    except OSError:
        # Conservative: treat unknown OS errors as "alive".
        return True
    return True


def _is_pid_alive_windows(pid: int) -> bool:  # pragma: no cover - Windows-only
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_INVALID_PARAMETER = 87

        # ``use_last_error=True`` is required so ``get_last_error()``
        # reflects the failure code from the last kernel32 call rather
        # than a stale ``errno``.
        kernel32 = ctypes.WinDLL(  # type: ignore[attr-defined]
            "kernel32", use_last_error=True,
        )
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
        )
        if not handle:
            # ERROR_INVALID_PARAMETER (87) → no such PID.
            last = ctypes.get_last_error()
            if last == ERROR_INVALID_PARAMETER:
                return False
            # Access denied etc. → conservatively treat as alive.
            return True
        try:
            exit_code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                return True
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        # If ctypes plumbing itself fails, refuse to declare the PID
        # dead — better to block install than to break an active one.
        return True


def _read_lock_metadata(lock_path: Path) -> LockMetadata | None:
    """Parse the lock file. Returns None on any parse problem.

    A None return signals "malformed" and callers must treat the lock
    as held (conservative).
    """
    try:
        raw = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        pid = int(raw["pid"])
        hostname = str(raw["hostname"])
        created_at = str(raw["created_at"])
        operation = str(raw["operation"])
    except (KeyError, TypeError, ValueError):
        return None
    return LockMetadata(
        pid=pid,
        hostname=hostname,
        created_at=created_at,
        operation=operation,
    )


def _is_stale(
    md: LockMetadata,
    stale_seconds: int,
    now_epoch: float,
) -> tuple[bool, str]:
    """Return (is_stale, reason) for a parsed lock's metadata."""
    created_epoch = _parse_iso_utc(md.created_at)
    if created_epoch is None:
        # Timestamps we cannot parse do not count as "old"; fall through.
        return False, ""
    age = now_epoch - created_epoch
    if age >= stale_seconds:
        return True, f"age {age:.0f}s exceeds stale_seconds={stale_seconds}"
    # Same-host live-PID probe. Cross-host locks are never broken by
    # PID (we cannot introspect a remote PID table).
    try:
        this_host = platform.node()
    except Exception:
        this_host = ""
    if this_host and md.hostname == this_host and not _is_pid_alive(md.pid):
        return True, f"pid {md.pid} on this host is not alive"
    return False, ""


def _write_lock_atomic(lock_path: Path, payload: dict[str, object]) -> None:
    """Atomically create ``lock_path`` with ``payload`` as JSON.

    Raises :class:`FileExistsError` if the file already exists.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, indent=2, sort_keys=False) + "\n").encode("utf-8")
    # O_EXCL guarantees atomic creation across POSIX + Windows.
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        # If write failed after successful open, drop the file so a
        # retry can succeed. Best-effort.
        try:
            os.unlink(str(lock_path))
        except OSError:
            pass
        raise


def _try_break_stale(lock_path: Path, stale_seconds: int) -> tuple[bool, str]:
    """If the current lock file is stale, remove it. Returns (broken, note)."""
    md = _read_lock_metadata(lock_path)
    if md is None:
        # Conservative: unparseable = held.
        return False, "lock contents malformed; treating as active"
    stale, why = _is_stale(md, stale_seconds, time.time())
    if not stale:
        return False, ""
    try:
        os.unlink(str(lock_path))
    except FileNotFoundError:
        # Someone else already cleaned it up; that is fine.
        return True, f"stale lock ({why}) already removed by another process"
    except OSError as exc:
        return False, f"could not remove stale lock: {exc}"
    return True, f"broke stale lock ({why})"


@contextmanager
def model_lock(
    lock_path: Path,
    *,
    operation: str,
    stale_seconds: int = _DEFAULT_STALE_SECONDS,
) -> Iterator[LockMetadata]:
    """Acquire a filesystem lock at ``lock_path`` for ``operation``.

    Yields the metadata written into the lock file. Releases the lock
    in a ``finally`` block, even on exception.

    Raises :class:`LockHeld` if the lock is currently held by another
    live process (after any stale-detection break).

    Raises :class:`ValueError` on an unknown ``operation``.
    """
    if operation not in _ALLOWED_OPERATIONS:
        raise ValueError(
            f"operation must be one of {sorted(_ALLOWED_OPERATIONS)}, got {operation!r}"
        )

    lock_path = Path(lock_path)
    my_pid: int = os.getpid()
    my_hostname: str = platform.node() or ""
    my_created_at: str = _now_iso_utc()
    payload: dict[str, object] = {
        "pid": my_pid,
        "hostname": my_hostname,
        "created_at": my_created_at,
        "operation": operation,
    }

    try:
        _write_lock_atomic(lock_path, payload)
    except FileExistsError:
        # Try to break a stale lock before giving up.
        broken, note = _try_break_stale(lock_path, stale_seconds)
        if broken:
            _logger.info("model_lock: %s", note)
            try:
                _write_lock_atomic(lock_path, payload)
            except FileExistsError as exc2:
                md = _read_lock_metadata(lock_path)
                raise LockHeld(
                    lock_path=lock_path,
                    holder=(
                        {
                            "pid": md.pid,
                            "hostname": md.hostname,
                            "created_at": md.created_at,
                            "operation": md.operation,
                        }
                        if md is not None
                        else None
                    ),
                    message=(
                        f"model lock at {lock_path} became re-held after "
                        f"stale-break attempt: {exc2}"
                    ),
                ) from exc2
        else:
            md = _read_lock_metadata(lock_path)
            holder = (
                {
                    "pid": md.pid,
                    "hostname": md.hostname,
                    "created_at": md.created_at,
                    "operation": md.operation,
                }
                if md is not None
                else None
            )
            msg = (
                f"model lock at {lock_path} is held by another process; "
                f"{note or 'lock is active'}"
            )
            raise LockHeld(lock_path=lock_path, holder=holder, message=msg)
    except OSError as exc:
        # Includes disk-full errors on write. Surface as-is; caller
        # will surface an actionable message.
        if exc.errno == errno.ENOSPC:
            raise
        raise

    md = LockMetadata(
        pid=my_pid,
        hostname=my_hostname,
        created_at=my_created_at,
        operation=operation,
    )
    try:
        yield md
    finally:
        try:
            os.unlink(str(lock_path))
        except FileNotFoundError:
            # Somebody else already cleaned up; treat as OK.
            _logger.warning(
                "model_lock: lock file %s missing at release; another actor may have removed it",
                lock_path,
            )
        except OSError as exc:
            _logger.warning(
                "model_lock: failed to release lock %s: %s", lock_path, exc,
            )
