"""Parser workers.

Each worker binds the smoke to one real parser package. The workers
are launched as subprocesses by ``real_adapters.SubprocessParserAdapter``;
they are NOT imported by any synthetic test in this PR because the
test conftest hard-blocks real parser package imports. Real coverage
happens only at B1a-7b.2b real-smoke authorization time.

The workers deliberately do not touch a smoke payload's identity —
they only see the canonical_id from CLI args and the PDF bytes on
stdin. No cross-parser state leaks across worker processes because
each worker is a fresh child process.
"""
from __future__ import annotations
