import os as _os
from importlib.metadata import PackageNotFoundError, version

# Issue #109: pymupdf 1.28+ writes a `fitz` deprecation warning and a
# `pymupdf_layout` suggestion to *stdout* at module import time
# (message stream defaults to stdout inside `pymupdf/__init__.py`).
# Anything that imports aksharamd — including tests, the MCP server,
# and the CLI — indirectly imports fitz via the parser registry, which
# then poisons stdout so `aksharamd validate --json` / `compile --json`
# no longer produces parseable JSON. Redirect the pymupdf message
# stream to OS-level stderr (`fd:2`) via the documented env var BEFORE
# any submodule that could trigger `import fitz` runs. Users can still
# override by exporting PYMUPDF_MESSAGE themselves. The individual
# guard in `aksharamd/plugins/parsers/pdf.py` remains for defence in
# depth (older callers may import that module directly).
_os.environ.setdefault("PYMUPDF_MESSAGE", "fd:2")
_os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")

try:
    __version__ = version("aksharamd")
except PackageNotFoundError:
    __version__ = "0.0.0.dev"
