"""Delivered text, rather than unformatted block content, defines local counts."""
from __future__ import annotations

import json

import pytest

from aksharamd.compiler import Compiler
from aksharamd.utils import count_tokens


@pytest.mark.parametrize("markdown", [
    "```python\nprint(42)\n```",
    "```python\nx\n \ny\n```",
    "### Quarterly revenue",
    "| Product | Revenue |\n| --- | --- |\n| Alpha | USD 125 |",
    "# Report\n\n```python\nprint(42)\n```\n\n> Evidence survives.",
])
def test_manifest_counts_exact_string_and_export(markdown, tmp_path):
    source = tmp_path / "source.md"
    source.write_text(markdown, encoding="utf-8")
    compiler = Compiler(output_dir=str(tmp_path / "out"))
    text, string_ctx = compiler.compile_to_string(str(source))
    export_ctx = compiler.compile(str(source))
    exported = (tmp_path / "out" / "document.md").read_bytes().decode("utf-8")
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert text == exported
    assert string_ctx.manifest.optimized_tokens == count_tokens(text)
    assert export_ctx.manifest.optimized_tokens == count_tokens(exported)
    assert manifest["optimized_tokens"] == count_tokens(exported)


def test_corpus_budget_includes_markdown_framing(tmp_path):
    source = tmp_path / "inputs"
    source.mkdir()
    (source / "a.md").write_text("```python\nprint(42)\n```", encoding="utf-8")
    (source / "b.md").write_text("### Quarterly revenue outlook", encoding="utf-8")
    compiler = Compiler(output_dir=str(tmp_path / "out"))
    texts = [compiler.compile_to_string(str(path))[0] for path in sorted(source.iterdir())]
    budget = max(count_tokens(text) for text in texts)
    result = compiler.compile_corpus(str(source), token_budget=budget)
    assert result.processed == 2
    assert len(result.chunks) == 2
    for chunk in result.chunks:
        assert chunk["token_count"] <= budget
        assert chunk["token_count"] == sum(count_tokens(d["text"]) for d in chunk["documents"])
        for doc in chunk["documents"]:
            assert doc["tokens"] == count_tokens(doc["text"])
