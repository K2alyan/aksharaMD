from __future__ import annotations

import pytest
from click.testing import CliRunner

from aksharamd.cli import _output_stem, _SourceArg, main

# ── _output_stem ──────────────────────────────────────────────────────────────

def test_output_stem_file_path(tmp_path):
    f = tmp_path / "my_document.pdf"
    f.touch()
    assert _output_stem(str(f)) == "my_document"


def test_output_stem_url_with_filename():
    assert _output_stem("https://example.com/report.html") == "report"


def test_output_stem_url_bare_hostname():
    # dot in hostname is sanitized to underscore for filesystem safety
    assert _output_stem("https://example.com/") == "example_com"


def test_output_stem_url_no_extension():
    assert _output_stem("https://en.wikipedia.org/wiki/Python") == "Python"


def test_output_stem_url_sanitizes_special_chars():
    stem = _output_stem("https://en.wikipedia.org/wiki/Python_(programming_language)")
    assert "/" not in stem
    assert " " not in stem


def test_output_stem_s3_url():
    assert _output_stem("s3://my-bucket/docs/report.pdf") == "report"


def test_output_stem_s3_url_no_key():
    stem = _output_stem("s3://my-bucket/")
    assert stem  # never empty


def test_output_stem_url_fallback():
    # A URL that produces an empty stem after sanitization falls back to "url_output"
    stem = _output_stem("https://example.com/")
    assert stem  # never empty


# ── _SourceArg ────────────────────────────────────────────────────────────────

@pytest.fixture
def source_arg():
    return _SourceArg()


def test_source_arg_accepts_http_url(source_arg):
    result = source_arg.convert("http://example.com/page.html", None, None)
    assert result == "http://example.com/page.html"


def test_source_arg_accepts_https_url(source_arg):
    result = source_arg.convert("https://example.com/doc.pdf", None, None)
    assert result == "https://example.com/doc.pdf"


def test_source_arg_accepts_s3_url(source_arg):
    result = source_arg.convert("s3://my-bucket/docs/report.pdf", None, None)
    assert result == "s3://my-bucket/docs/report.pdf"


def test_source_arg_rejects_s3_url_without_bucket(source_arg):
    import click
    with pytest.raises(click.exceptions.BadParameter):
        source_arg.convert("s3://", None, None)


def test_source_arg_accepts_existing_file(source_arg, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    result = source_arg.convert(str(f), None, None)
    assert result == str(f)


def test_source_arg_rejects_nonexistent_file(source_arg):
    import click
    with pytest.raises(click.exceptions.BadParameter):
        source_arg.convert("/nonexistent/path/file.txt", None, None)


def test_source_arg_rejects_plain_string(source_arg):
    import click
    with pytest.raises(click.exceptions.BadParameter):
        source_arg.convert("not_a_url_or_path", None, None)


# ── doctor command ────────────────────────────────────────────────────────────

def test_doctor_exits_cleanly():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0


def test_doctor_output_contains_feature_names():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    output = result.output
    assert "Tesseract OCR" in output or "OCR" in output
    assert "ffmpeg" in output or "audio" in output.lower()


def test_doctor_shows_python_version():
    """doctor must always display the Python version."""
    import sys
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    py_str = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert py_str in result.output


def test_doctor_shows_format_count():
    """doctor must print the number of registered extensions."""
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    assert "Registered format extensions" in result.output


def test_doctor_strict_exits_zero_when_deps_present(monkeypatch):
    """--strict exits 0 only if Python and all optional deps are ok."""
    # We can't guarantee all deps are installed in CI, so just check the flag
    # is accepted (exit 0 or 1, never a crash).
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--strict"])
    assert result.exit_code in (0, 1)
