from __future__ import annotations

import os

import pytest

from aksharamd.compiler import Compiler
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
