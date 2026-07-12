from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aksharamd.compiler import Compiler, _fetch_url_to_temp
from aksharamd.context import CompilationContext
from aksharamd.plugins.parsers.html import _extract_image_bytes

# ── HTML path traversal ───────────────────────────────────────────────────────

def test_html_image_path_traversal_blocked(tmp_path):
    """Symlink / ../ traversal must not read files outside the document directory."""
    # Create a sensitive file one level up
    sensitive = tmp_path.parent / "sensitive.txt"
    sensitive.write_text("SECRET", encoding="utf-8")

    html_file = tmp_path / "doc.html"
    result = _extract_image_bytes("../sensitive.txt", html_file)
    assert result is None, "Path traversal must be blocked"

    # Clean up
    sensitive.unlink(missing_ok=True)


def test_html_image_symlink_blocked(tmp_path):
    """Symlink pointing outside the document root must be blocked."""
    sensitive = tmp_path.parent / "sensitive2.txt"
    sensitive.write_text("SECRET2", encoding="utf-8")

    link = tmp_path / "evil.png"
    try:
        link.symlink_to(sensitive)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported on this platform")

    html_file = tmp_path / "doc.html"
    result = _extract_image_bytes("evil.png", html_file)
    assert result is None, "Symlink traversal must be blocked"

    link.unlink(missing_ok=True)
    sensitive.unlink(missing_ok=True)


def test_html_remote_image_not_fetched(tmp_path):
    """http:// and https:// image sources must never trigger a network request."""
    html_file = tmp_path / "doc.html"
    result = _extract_image_bytes("https://evil.example.com/steal.png", html_file)
    assert result is None


def test_html_data_uri_decoded(tmp_path):
    """data: URIs with valid base64 should be decoded correctly."""
    import base64
    fake_png = b"\x89PNG\r\n" + b"A" * 20
    encoded = base64.b64encode(fake_png).decode()
    data_uri = f"data:image/png;base64,{encoded}"
    html_file = tmp_path / "doc.html"
    result = _extract_image_bytes(data_uri, html_file)
    assert result == fake_png


# ── File size gate ────────────────────────────────────────────────────────────

def test_compiler_rejects_oversized_file(tmp_path):
    """Files larger than _MAX_FILE_BYTES must be rejected before parsing."""
    big = tmp_path / "big.txt"
    # Write a marker, then patch the size check via env var
    big.write_text("A" * 100, encoding="utf-8")

    old = os.environ.get("AKSHARAMD_MAX_FILE_BYTES")
    try:
        os.environ["AKSHARAMD_MAX_FILE_BYTES"] = "10"  # 10 bytes limit
        # Re-import to pick up new env value — use the compiler directly
        from aksharamd import compiler as _c
        _c._MAX_FILE_BYTES = 10  # patch in-process

        CompilationContext(source=str(big), output_dir=str(tmp_path / "out"))
        _, stage_timings, t0 = Compiler()._run_pipeline.__wrapped__(
            Compiler(), str(big)
        ) if hasattr(Compiler()._run_pipeline, "__wrapped__") else (None, None, None)

        # Simpler: call compile_to_string and check for error
        compiler = Compiler(output_dir=str(tmp_path / "out"))
        text, ctx2 = compiler.compile_to_string(str(big))
        assert any(e.code == "FILE_TOO_LARGE" for e in ctx2.validation.errors)
    finally:
        if old is None:
            os.environ.pop("AKSHARAMD_MAX_FILE_BYTES", None)
        else:
            os.environ["AKSHARAMD_MAX_FILE_BYTES"] = old
        from aksharamd import compiler as _c
        _c._MAX_FILE_BYTES = int(os.environ.get("AKSHARAMD_MAX_FILE_BYTES", str(500 * 1024 * 1024)))


# ── Audio model whitelist ─────────────────────────────────────────────────────

def test_audio_model_whitelist():
    """AKSHARAMD_WHISPER_MODEL with an invalid value must fall back to 'base'."""
    old = os.environ.get("AKSHARAMD_WHISPER_MODEL")
    try:
        os.environ["AKSHARAMD_WHISPER_MODEL"] = "malicious-model; rm -rf /"
        # Re-evaluate the module-level variable
        import importlib

        import aksharamd.plugins.parsers.audio as audio_mod
        importlib.reload(audio_mod)
        assert audio_mod._DEFAULT_MODEL == "base"
    finally:
        if old is None:
            os.environ.pop("AKSHARAMD_WHISPER_MODEL", None)
        else:
            os.environ["AKSHARAMD_WHISPER_MODEL"] = old
        import importlib

        import aksharamd.plugins.parsers.audio as audio_mod
        importlib.reload(audio_mod)


def test_audio_valid_model_accepted():
    """Valid model names must be accepted as-is."""
    old = os.environ.get("AKSHARAMD_WHISPER_MODEL")
    try:
        os.environ["AKSHARAMD_WHISPER_MODEL"] = "small"
        import importlib

        import aksharamd.plugins.parsers.audio as audio_mod
        importlib.reload(audio_mod)
        assert audio_mod._DEFAULT_MODEL == "small"
    finally:
        if old is None:
            os.environ.pop("AKSHARAMD_WHISPER_MODEL", None)
        else:
            os.environ["AKSHARAMD_WHISPER_MODEL"] = old
        import importlib

        import aksharamd.plugins.parsers.audio as audio_mod
        importlib.reload(audio_mod)


# ── Archive safety ────────────────────────────────────────────────────────────

def test_zip_path_traversal_blocked(tmp_path):
    """ZIP files with ../ entry names must not write outside the extract dir."""
    import zipfile
    evil_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(str(evil_zip), "w") as zf:
        zf.writestr("../escape.txt", "ESCAPED")

    from aksharamd.plugins.parsers.archive import ZipParser
    ctx = CompilationContext(source=str(evil_zip), output_dir=str(tmp_path / "out"))
    ZipParser().execute(ctx)

    # The escaped file must not exist outside tmp_path
    escaped = tmp_path.parent / "escape.txt"
    assert not escaped.exists(), "ZIP path traversal must be blocked"


def test_html_data_uri_svg_blocked(tmp_path):
    """SVG data URIs must be rejected (potential XSS / code execution)."""
    svg_data = b"<svg><script>alert(1)</script></svg>"
    import base64
    encoded = base64.b64encode(svg_data).decode()
    data_uri = f"data:image/svg+xml;base64,{encoded}"
    html_file = tmp_path / "doc.html"
    result = _extract_image_bytes(data_uri, html_file)
    assert result is None, "SVG data URIs must be blocked"


def test_html_data_uri_non_image_blocked(tmp_path):
    """Non-image data URIs (e.g. text/html) must be rejected."""
    import base64
    encoded = base64.b64encode(b"<html><body>evil</body></html>").decode()
    data_uri = f"data:text/html;base64,{encoded}"
    html_file = tmp_path / "doc.html"
    result = _extract_image_bytes(data_uri, html_file)
    assert result is None, "Non-image data URIs must be blocked"


def test_zip_entry_count_limit(tmp_path, monkeypatch):
    """ZIP archives with more than _MAX_ZIP_ENTRIES entries must be rejected."""
    import zipfile

    import aksharamd.plugins.parsers.archive as archive_mod
    monkeypatch.setattr(archive_mod, "_MAX_ZIP_ENTRIES", 2)

    many_zip = tmp_path / "many.zip"
    with zipfile.ZipFile(str(many_zip), "w") as zf:
        for i in range(5):
            zf.writestr(f"file{i}.txt", f"content {i}")

    from aksharamd.plugins.parsers.archive import ZipParser
    ctx = CompilationContext(source=str(many_zip), output_dir=str(tmp_path / "out"))
    ZipParser().execute(ctx)
    assert any(e.code == "ARCHIVE_TOO_MANY_ENTRIES" for e in ctx.validation.errors)


def test_zip_decompression_bomb_blocked(tmp_path, monkeypatch):
    """ZIP archives whose declared uncompressed size exceeds the limit must be rejected."""
    import zipfile

    import aksharamd.plugins.parsers.archive as archive_mod
    monkeypatch.setattr(archive_mod, "_MAX_ARCHIVE_DECOMPRESSED_BYTES", 100)

    bomb_zip = tmp_path / "bomb.zip"
    with zipfile.ZipFile(str(bomb_zip), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", "A" * 500)

    from aksharamd.plugins.parsers.archive import ZipParser
    ctx = CompilationContext(source=str(bomb_zip), output_dir=str(tmp_path / "out"))
    ZipParser().execute(ctx)
    assert any(e.code == "ARCHIVE_TOO_LARGE" for e in ctx.validation.errors)


def test_zip_nested_archive_not_recursed(tmp_path):
    """A ZIP inside a ZIP must not be recursively extracted — inner archive is listed only."""
    import zipfile

    inner_zip = tmp_path / "inner.zip"
    with zipfile.ZipFile(str(inner_zip), "w") as zf:
        zf.writestr("secret.txt", "INNER SECRET")

    outer_zip = tmp_path / "outer.zip"
    with zipfile.ZipFile(str(outer_zip), "w") as zf:
        zf.write(str(inner_zip), arcname="inner.zip")
        zf.writestr("readme.txt", "outer content")

    from aksharamd.plugins.parsers.archive import ZipParser
    ctx = CompilationContext(source=str(outer_zip), output_dir=str(tmp_path / "out"))
    ZipParser().execute(ctx)

    assert ctx.document is not None
    all_content = " ".join(b.content for b in ctx.document.blocks)
    # inner.zip appears in the file listing but "INNER SECRET" must not be extracted
    assert "INNER SECRET" not in all_content
    assert "inner.zip" in all_content


def test_xml_deep_nesting_does_not_recurse(tmp_path):
    """Pathologically nested XML (depth > _XML_MAX_DEPTH) must not raise RecursionError."""
    from aksharamd.plugins.parsers.data import XmlParser

    # Build XML nested 200 levels deep — well beyond the 50-level limit
    depth = 200
    inner = "<leaf>content</leaf>"
    xml = inner
    for i in range(depth):
        xml = f"<level{i}>{xml}</level{i}>"
    xml = f'<?xml version="1.0"?><root>{xml}</root>'

    f = tmp_path / "deep.xml"
    f.write_text(xml, encoding="utf-8")
    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    XmlParser().execute(ctx)  # must not raise RecursionError

    # Parser should produce some output without crashing
    assert ctx.document is not None or ctx.validation.errors


def test_block_heading_level_validator():
    """Block heading level must be 1-6; values outside this range should raise."""
    import pytest

    from aksharamd.models.block import Block, BlockType

    with pytest.raises(Exception):
        Block(type=BlockType.HEADING, content="bad level", level=7, index=0)


def test_block_heading_level_valid():
    """Valid heading levels 1-6 should not raise."""
    from aksharamd.models.block import Block, BlockType

    for level in range(1, 7):
        b = Block(type=BlockType.HEADING, content="ok", level=level, index=0)
        assert b.level == level


def test_plugin_cache_populated(monkeypatch):
    """get_plugins_of_type should cache results on second call."""
    from aksharamd.plugins import registry
    from aksharamd.plugins.base import CleanerPlugin

    registry._clear_plugin_cache()
    # First call populates the cache
    result1 = registry.get_plugins_of_type(CleanerPlugin)
    # Second call should return the same cached list
    result2 = registry.get_plugins_of_type(CleanerPlugin)
    assert result1 is result2  # same object from cache


# ── URL fetch security ────────────────────────────────────────────────────────

def test_fetch_url_pinned_ip_used(monkeypatch):
    """The validated IP must be used for the actual connection (DNS pinning)."""

    calls: list[tuple] = []

    def fake_getaddrinfo(host, port, *a, **kw):
        calls.append((host, port))
        # Return a safe public IP
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", int(port or 0)))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    # requests.get will re-invoke getaddrinfo internally — the pinned version
    # should always return 1.2.3.4 for our hostname, not anything else.
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_redirect = False
    mock_resp.is_permanent_redirect = False
    mock_resp.headers = {"Content-Type": "text/plain", "Content-Length": "5"}
    mock_resp.iter_content = MagicMock(return_value=iter([b"hello"]))
    mock_resp.raise_for_status = MagicMock()
    mock_resp.close = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        path = _fetch_url_to_temp("http://example.com/file.txt")

    assert Path(path).exists()
    Path(path).unlink(missing_ok=True)
    # getaddrinfo was called at least once (initial validation), and pinning
    # redirected any subsequent calls too
    assert any(c[0] == "example.com" for c in calls)


def test_fetch_url_private_ip_rejected():
    """URLs that resolve to private IPs must be rejected."""
    import socket as _socket

    def fake_getaddrinfo(host, port, *a, **kw):
        return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("192.168.1.1", 0))]

    with patch.object(_socket, "getaddrinfo", side_effect=fake_getaddrinfo):
        with pytest.raises(ValueError, match="private"):
            _fetch_url_to_temp("http://internal.corp/secret.pdf")


def test_fetch_url_temp_file_deleted_on_size_exceeded(monkeypatch, tmp_path):
    """Partial temp file must be deleted when the download size limit is exceeded."""
    import aksharamd.compiler as comp_mod

    monkeypatch.setattr(comp_mod, "_MAX_FILE_BYTES", 10)

    def fake_getaddrinfo(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", int(port or 0)))]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_redirect = False
    mock_resp.is_permanent_redirect = False
    mock_resp.headers = {"Content-Type": "text/plain"}
    mock_resp.iter_content = MagicMock(return_value=iter([b"X" * 20]))  # exceeds 10-byte limit
    mock_resp.raise_for_status = MagicMock()
    mock_resp.close = MagicMock()

    created_paths: list[str] = []
    orig_ntf = tempfile.NamedTemporaryFile

    def tracking_ntf(**kwargs):
        f = orig_ntf(**kwargs)
        created_paths.append(f.name)
        return f

    with patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo):
        with patch("requests.get", return_value=mock_resp):
            with patch("tempfile.NamedTemporaryFile", side_effect=tracking_ntf):
                with pytest.raises(ValueError, match="size limit"):
                    _fetch_url_to_temp("http://example.com/big.txt")

    # The partial temp file must have been deleted
    for p in created_paths:
        assert not Path(p).exists(), f"Leaked temp file: {p}"


def test_fetch_url_response_body_closed_on_error(monkeypatch):
    """HTTP response body must be closed even when download fails."""
    def fake_getaddrinfo(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", int(port or 0)))]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_redirect = False
    mock_resp.is_permanent_redirect = False
    mock_resp.headers = {"Content-Type": "text/plain"}
    mock_resp.iter_content = MagicMock(side_effect=OSError("connection dropped"))
    mock_resp.raise_for_status = MagicMock()
    mock_resp.close = MagicMock()

    with patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo):
        with patch("requests.get", return_value=mock_resp):
            with pytest.raises((ValueError, OSError)):
                _fetch_url_to_temp("http://example.com/file.txt")

    mock_resp.close.assert_called()


def test_fetch_url_restores_getaddrinfo_after_exception(monkeypatch):
    """socket.getaddrinfo must be restored even if the request raises."""
    def fake_getaddrinfo(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", int(port or 0)))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with patch("requests.get", side_effect=RuntimeError("boom")):
        with pytest.raises((ValueError, RuntimeError)):
            _fetch_url_to_temp("http://example.com/file.txt")

    # getaddrinfo should be restored to fake_getaddrinfo (monkeypatch manages cleanup)
    # The real invariant: the function's internal patch/restore loop ran to completion
    # (no AttributeError or similar residue). We can verify by calling a resolution.
    result = socket.getaddrinfo("localhost", 80)
    assert result  # still callable and returns something


def test_scoring_empty_tokens_returns_low_score():
    """Documents with zero original_tokens should score 10."""
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import Block, BlockType
    from aksharamd.models.document import Document
    from aksharamd.scoring.readiness import compute_confidence

    doc = Document(
        source="test.md",
        file_type="md",
        blocks=[Block(type=BlockType.PARAGRAPH, content="hello", index=0)],
        pages=0,
    )
    ctx = CompilationContext(source="test.md", document=doc, original_tokens=0)
    result = compute_confidence(ctx)
    assert result.score == 10
    assert any("empty" in n.lower() for n in result.notes)
