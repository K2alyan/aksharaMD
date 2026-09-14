"""Tests for raw source bytes access on CompilationContext (P0.1).

Substance detectors that compare source-vs-parsed (Tier 3 geometric
cross-reference, dropped-region detection) need access to the raw source
bytes. This test locks:

  - the population contract at the compiler boundary (both built-in and
    adapter paths mirror bytes onto ``ctx.raw_source_bytes``);
  - the lazy-fallback semantics of the ``ctx.raw_bytes()`` helper for
    direct-invocation callers that construct their own CompilationContext;
  - the safety guards (missing paths, directories, oversize files, URLs).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from aksharamd.compiler import Compiler
from aksharamd.context import CompilationContext
from aksharamd.parser_contract import ParsedArtifact, ParserInput


class _EchoAdapter:
    """Trivial parser adapter that echoes its input as text/markdown."""

    def parse(self, source: ParserInput) -> ParsedArtifact:
        content = b"# Echoed\n\nParsed by test echo adapter.\n"
        return ParsedArtifact(
            source_id=source.source_id,
            source_hash=source.source_hash,
            content=content,
            content_hash=hashlib.sha256(content).hexdigest(),
            content_mime_type="text/markdown",
            parser_name="echo-test",
        )


# ── Field defaults ──────────────────────────────────────────────────────────

def test_raw_source_bytes_defaults_to_none():
    ctx = CompilationContext(source="test.pdf")
    assert ctx.raw_source_bytes is None


def test_raw_bytes_helper_returns_none_when_source_empty():
    ctx = CompilationContext(source="")
    assert ctx.raw_bytes() is None


def test_raw_bytes_helper_returns_none_for_missing_path():
    ctx = CompilationContext(source="/does/not/exist.pdf")
    assert ctx.raw_bytes() is None


def test_raw_bytes_helper_returns_none_for_directory(tmp_path: Path):
    ctx = CompilationContext(source=str(tmp_path))
    assert ctx.raw_bytes() is None


# ── Populated-field access ──────────────────────────────────────────────────

def test_raw_bytes_helper_returns_field_when_populated():
    ctx = CompilationContext(source="test.pdf")
    ctx.raw_source_bytes = b"hello world"
    assert ctx.raw_bytes() == b"hello world"


def test_raw_bytes_helper_prefers_field_over_lazy_read(tmp_path: Path):
    """When the field is set, the helper returns it and does not re-read disk."""
    p = tmp_path / "sample.md"
    p.write_bytes(b"on-disk content")
    ctx = CompilationContext(source=str(p))
    ctx.raw_source_bytes = b"field content"
    assert ctx.raw_bytes() == b"field content"


# ── Lazy fallback ───────────────────────────────────────────────────────────

def test_raw_bytes_helper_lazy_reads_local_file(tmp_path: Path):
    p = tmp_path / "sample.md"
    payload = b"# Sample\n\nSome content.\n"
    p.write_bytes(payload)
    ctx = CompilationContext(source=str(p))
    assert ctx.raw_bytes() == payload


def test_raw_bytes_helper_memoizes_lazy_read(tmp_path: Path):
    """After a successful lazy read the field is set and disk is not re-read."""
    p = tmp_path / "sample.md"
    payload = b"original"
    p.write_bytes(payload)
    ctx = CompilationContext(source=str(p))
    first = ctx.raw_bytes()
    assert first == payload
    p.write_bytes(b"mutated on disk")
    assert ctx.raw_bytes() == payload  # memoized value survives disk mutation
    assert ctx.raw_source_bytes == payload


# ── repr safety ─────────────────────────────────────────────────────────────

def test_raw_source_bytes_excluded_from_repr():
    """A ~50 MB byte buffer in the default repr would be catastrophic."""
    ctx = CompilationContext(source="test.pdf")
    ctx.raw_source_bytes = b"secret sensitive bytes"
    rendered = repr(ctx)
    assert "secret" not in rendered
    assert "sensitive" not in rendered


# ── Compiler-driven population ──────────────────────────────────────────────

def test_compiler_populates_raw_source_bytes_builtin_parser(tmp_path: Path):
    """Non-adapter path: compiler reads bytes for hashing and mirrors onto ctx."""
    md = tmp_path / "sample.md"
    payload = b"# hello\n\nworld\n"
    md.write_bytes(payload)
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(md))
    assert ctx.raw_source_bytes == payload
    # Existing capture_id contract still holds.
    assert ctx.capture_id == hashlib.sha256(payload).hexdigest()


def test_compiler_populates_raw_source_bytes_adapter_path(tmp_path: Path):
    """Adapter path: compiler mirrors source bytes onto ctx before invoking adapter."""
    md = tmp_path / "sample.md"
    payload = b"# hello\n\nadapter path\n"
    md.write_bytes(payload)
    ctx = Compiler(
        output_dir=str(tmp_path / "out"),
        parser_adapter=_EchoAdapter(),
    ).compile(str(md))
    assert ctx.raw_source_bytes == payload
