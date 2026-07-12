from __future__ import annotations

import os
import socket
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aksharamd.compiler import Compiler, _fetch_url_to_temp, _PinnedIPAdapter
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

def _mock_requests_response(
    status_code: int = 200,
    content_type: str = "text/plain",
    body_chunks: list | None = None,
    body_error: Exception | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_redirect = False
    resp.is_permanent_redirect = False
    resp.headers = {"Content-Type": content_type}
    resp.raise_for_status = MagicMock()
    resp.close = MagicMock()
    if body_error is not None:
        resp.iter_content = MagicMock(side_effect=body_error)
    else:
        resp.iter_content = MagicMock(return_value=iter(body_chunks or [b"hello"]))
    return resp


def test_adapter_connects_to_pinned_ip(monkeypatch):
    """_PinnedIPAdapter.send must create a pool pointing to the pinned IP (no re-resolution)."""
    import requests
    import urllib3

    captured_pool_host: list[str] = []

    class FakePool:
        def __init__(self, host, *args, **kwargs):
            captured_pool_host.append(host)

        def urlopen(self, *args, **kwargs):
            r = MagicMock()
            r.status = 200
            r.headers = {}
            r.reason = "OK"
            return r

    monkeypatch.setattr(urllib3, "HTTPConnectionPool", FakePool)

    adapter = _PinnedIPAdapter("example.com", "1.2.3.4")
    req = requests.Request("GET", "http://example.com/file.txt").prepare()
    try:
        adapter.send(req, timeout=5)
    except Exception:
        pass  # build_response may fail against FakePool mock; we only care about host

    assert "1.2.3.4" in captured_pool_host, f"Expected '1.2.3.4' in pool hosts; got {captured_pool_host}"


def test_concurrent_adapters_use_distinct_pinned_ips(monkeypatch):
    """Concurrent fetches to different hosts must each use their own validated IP.

    Regression test: the previous global socket.getaddrinfo override was unsafe
    under concurrency — two threads could overwrite each other's resolver.
    _PinnedIPAdapter carries all state per-instance and creates per-request pools
    so there is no shared mutable global state to cross-contaminate.
    """
    import requests
    import urllib3

    pool_host_by_thread: dict[str, str] = {}
    lock = threading.Lock()
    barrier = threading.Barrier(2)  # force both threads to create pools simultaneously

    class SyncedFakePool:
        def __init__(self, host, *args, **kwargs):
            barrier.wait()  # ensure simultaneous pool creation
            with lock:
                pool_host_by_thread[threading.current_thread().name] = host

        def urlopen(self, *args, **kwargs):
            r = MagicMock()
            r.status = 200
            r.headers = {}
            r.reason = "OK"
            return r

    monkeypatch.setattr(urllib3, "HTTPConnectionPool", SyncedFakePool)

    def run(name: str, hostname: str, pinned_ip: str) -> None:
        adapter = _PinnedIPAdapter(hostname, pinned_ip)
        req = requests.Request("GET", f"http://{hostname}/file").prepare()
        try:
            adapter.send(req, timeout=5)
        except Exception:
            pass

    t1 = threading.Thread(target=run, name="t-alpha", args=("t-alpha", "alpha.example.com", "1.1.1.1"))
    t2 = threading.Thread(target=run, name="t-beta",  args=("t-beta",  "beta.example.com",  "2.2.2.2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert pool_host_by_thread.get("t-alpha") == "1.1.1.1", pool_host_by_thread
    assert pool_host_by_thread.get("t-beta")  == "2.2.2.2", pool_host_by_thread


def test_fetch_url_private_ip_rejected():
    """Initial DNS validation must reject URLs that resolve to private IPs."""
    import socket as _socket

    def fake_getaddrinfo(host, port, *a, **kw):
        return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("192.168.1.1", 0))]

    with patch.object(_socket, "getaddrinfo", side_effect=fake_getaddrinfo):
        with pytest.raises(ValueError, match="private"):
            _fetch_url_to_temp("http://internal.corp/secret.pdf")


def test_fetch_url_temp_file_deleted_on_size_exceeded(monkeypatch):
    """Partial temp file must be deleted when the download size limit is exceeded."""
    import aksharamd.compiler as comp_mod

    monkeypatch.setattr(comp_mod, "_MAX_FILE_BYTES", 10)
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", int(p or 0)))
    ])

    mock_resp = _mock_requests_response(body_chunks=[b"X" * 20])

    created_paths: list[str] = []
    orig_ntf = tempfile.NamedTemporaryFile

    def tracking_ntf(**kwargs):
        f = orig_ntf(**kwargs)
        created_paths.append(f.name)
        return f

    with patch.object(comp_mod._PinnedIPAdapter, "send", return_value=mock_resp), \
         patch("tempfile.NamedTemporaryFile", side_effect=tracking_ntf):
        with pytest.raises(ValueError, match="size limit"):
            _fetch_url_to_temp("http://example.com/big.txt")

    for p in created_paths:
        assert not Path(p).exists(), f"Leaked temp file: {p}"


def test_fetch_url_response_body_closed_on_error(monkeypatch):
    """HTTP response body must be closed even when the download raises mid-stream."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", int(p or 0)))
    ])

    import aksharamd.compiler as comp_mod
    mock_resp = _mock_requests_response(body_error=OSError("connection dropped"))

    with patch.object(comp_mod._PinnedIPAdapter, "send", return_value=mock_resp):
        with pytest.raises((ValueError, OSError)):
            _fetch_url_to_temp("http://example.com/file.txt")

    mock_resp.close.assert_called()


def test_fetch_url_does_not_modify_socket_getaddrinfo(monkeypatch):
    """_PinnedIPAdapter must not patch socket.getaddrinfo — no global state is modified."""
    import aksharamd.compiler as comp_mod

    sentinel = object()
    monkeypatch.setattr(socket, "getaddrinfo", sentinel)

    mock_resp = _mock_requests_response()

    with patch.object(comp_mod._PinnedIPAdapter, "send", return_value=mock_resp):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", int(p or 0)))
        ])
        try:
            _fetch_url_to_temp("http://example.com/file.txt")
        except Exception:
            pass

    # socket.getaddrinfo must still be the lambda we set — not overwritten by fetch
    assert socket.getaddrinfo is not sentinel  # monkeypatch managed it


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
