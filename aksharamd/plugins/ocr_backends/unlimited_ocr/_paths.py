"""Cache-location helpers for the Unlimited-OCR safe-size cache.

Callers use these to locate, disable, or clear the persistent cache
that :func:`.portable.infer_pdf_portable` writes to.

Environment variables (all optional):

``AKSHARAMD_OCR_CACHE_PATH``
    Absolute path to the cache JSON file. Overrides the OS-default
    location returned by :func:`default_cache_path`.

``AKSHARAMD_OCR_CACHE_DISABLE``
    Set to ``"1"``, ``"true"``, or ``"yes"`` (case-insensitive) to
    disable cache reads and writes entirely. Callers should check
    :func:`is_cache_disabled` and pass ``cache_path=None`` to
    ``infer_pdf_portable`` when it returns True.

Design notes
------------

* Never raises on OS errors. Cache is best-effort; a permission or
  disk error must never break a benchmark or compile run.
* The default path deliberately lives OUTSIDE the model / HuggingFace
  cache so removing the model does not silently invalidate size
  history and vice versa.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_CACHE_PATH = "AKSHARAMD_OCR_CACHE_PATH"
_ENV_CACHE_DISABLE = "AKSHARAMD_OCR_CACHE_DISABLE"
_CACHE_FILENAME = "ocr_safe_size_cache.json"

_TRUE_VALUES = frozenset({"1", "true", "yes"})


def default_cache_path() -> Path:
    """Return the default on-disk location for the safe-size cache.

    Precedence:

    1. ``$AKSHARAMD_OCR_CACHE_PATH`` if set to a non-empty value.
    2. Windows: ``%LOCALAPPDATA%\\aksharamd\\ocr_safe_size_cache.json``,
       falling back to ``~/.cache/aksharamd/...`` if the env var is
       unset.
    3. POSIX: ``$XDG_CACHE_HOME/aksharamd/...`` if set, else
       ``~/.cache/aksharamd/...``.

    Never touches the filesystem. Callers wanting the directory
    created should do so themselves (the cache writer already
    ``mkdir(parents=True, exist_ok=True)``s).
    """
    override = os.environ.get(_ENV_CACHE_PATH, "").strip()
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app:
            return Path(local_app) / "aksharamd" / _CACHE_FILENAME
        return Path.home() / ".cache" / "aksharamd" / _CACHE_FILENAME

    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "aksharamd" / _CACHE_FILENAME
    return Path.home() / ".cache" / "aksharamd" / _CACHE_FILENAME


def is_cache_disabled() -> bool:
    """Return True if the operator has disabled the cache via env var.

    Recognised true values (case-insensitive): ``1``, ``true``, ``yes``.
    Any other value (including unset) means the cache is enabled.
    """
    value = os.environ.get(_ENV_CACHE_DISABLE, "").strip().lower()
    return value in _TRUE_VALUES


def clear_cache(path: Path | None = None) -> bool:
    """Delete the cache file if it exists.

    Returns True if a file was removed, False if it did not exist.
    Tolerates permission or OS errors by returning False — the cache
    is not critical, so a failure to clear must not raise into a
    caller's workflow.

    ``path=None`` targets :func:`default_cache_path`.
    """
    target = path or default_cache_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    except (OSError, PermissionError):
        return False
    return True
