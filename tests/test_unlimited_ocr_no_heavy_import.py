"""Guard tests: importing AksharaMD must not pull in torch / transformers.

The Unlimited-OCR runtime lives inside ``aksharamd.plugins.ocr_backends``
but its heavy dependencies (torch, transformers) are optional and only
imported lazily inside functions that actually need them. If a future
edit adds a top-level ``import torch`` anywhere in the load path,
``import aksharamd`` will start dragging ~1 GB of libraries into cold
startup — that's a large user-visible regression and these tests exist
to catch it in CI.

We run the check in a subprocess so pollution from earlier tests
(which may have imported torch for other reasons) can't hide a
regression.
"""
from __future__ import annotations

import subprocess
import sys


def test_importing_aksharamd_does_not_import_torch():
    """``import aksharamd`` must not put torch or transformers in sys.modules."""
    code = (
        "import sys, aksharamd\n"
        "assert 'torch' not in sys.modules, "
        "'torch was imported at aksharamd load — the Unlimited-OCR "
        "runtime must import it lazily inside its functions'\n"
        "assert 'transformers' not in sys.modules, "
        "'transformers was imported at aksharamd load — same rule as torch'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        "Subprocess check failed. stderr:\n" + result.stderr.decode("utf-8", errors="replace")
    )


def test_importing_unlimited_ocr_package_does_not_import_torch():
    """Importing the Unlimited-OCR runtime package (but no function in
    it) must not import torch or transformers.

    Once a caller invokes ``infer_pdf_portable`` — which internally
    reaches into the adapter and does a lazy ``import torch`` — torch
    will of course appear. That is fine and expected. What is NOT fine
    is triggering the heavy import merely by importing the package.
    """
    code = (
        "import sys\n"
        "import aksharamd.plugins.ocr_backends.unlimited_ocr  # noqa: F401\n"
        "assert 'torch' not in sys.modules, "
        "'torch was imported at unlimited_ocr package load'\n"
        "assert 'transformers' not in sys.modules, "
        "'transformers was imported at unlimited_ocr package load'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        "Subprocess check failed. stderr:\n" + result.stderr.decode("utf-8", errors="replace")
    )
