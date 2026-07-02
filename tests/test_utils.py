from __future__ import annotations

import pytest

from aksharamd.utils import count_tokens, format_savings_line, tokens_to_dollars


def test_count_tokens_with_real_text():
    n = count_tokens("Hello world this is a test.")
    assert n > 0


def test_count_tokens_empty_string():
    n = count_tokens("")
    assert n >= 0


def test_count_tokens_fallback_on_import_error(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("mocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    result = count_tokens("one two three four five")
    assert result == 5


def test_tokens_to_dollars_known_model():
    d = tokens_to_dollars(1_000_000, "gpt-4o")
    assert d == pytest.approx(2.50, abs=0.01)


def test_tokens_to_dollars_unknown_model_uses_default():
    d = tokens_to_dollars(1_000_000, "unknown-model-xyz")
    assert d == pytest.approx(2.50, abs=0.01)  # falls back to gpt-4o price


def test_tokens_to_dollars_zero():
    assert tokens_to_dollars(0, "gpt-4o") == 0.0


def test_format_savings_line_returns_string():
    line = format_savings_line(1000)
    assert isinstance(line, str)
    assert "$" in line


def test_format_savings_line_multiple_models():
    line = format_savings_line(1_000_000)
    assert "gpt-4o" in line
    assert "claude-sonnet-4" in line
