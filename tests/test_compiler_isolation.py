from pathlib import Path

from aksharamd.compiler import Compiler
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.plugins import registry
from aksharamd.plugins.base import CleanerPlugin, ExporterPlugin, ParserPlugin


def _parser(label: str):
    class ConfiguredParser(ParserPlugin):
        name = f"configured-{label}"
        supported_types = ["iso"]

        def execute(self, ctx):
            ctx.document = Document(
                source=ctx.source,
                file_type="iso",
                title=label,
                pages=1,
                blocks=[Block(type=BlockType.PARAGRAPH, content=label)],
            )
            ctx.document.compute_id()
            return ctx

    return ConfiguredParser


def test_compilers_keep_parser_selection_and_configuration_isolated(tmp_path: Path):
    source = tmp_path / "sample.iso"
    source.write_text("input", encoding="utf-8")
    first = Compiler(
        output_dir=str(tmp_path / "first"),
        parsers={"iso": _parser("one")},
        parser_configuration_id="config-one",
    )
    second = Compiler(
        output_dir=str(tmp_path / "second"),
        parsers={"iso": _parser("two")},
        parser_configuration_id="config-two",
    )

    first_text, first_ctx = first.compile_to_string(str(source))
    second_text, second_ctx = second.compile_to_string(str(source))

    assert "one" in first_text and "two" not in first_text
    assert "two" in second_text and "one" not in second_text
    assert first_ctx.parser_configuration_id == "config-one"
    assert second_ctx.parser_configuration_id == "config-two"


def test_late_global_registration_does_not_change_existing_compiler(tmp_path: Path):
    source = tmp_path / "sample.lateiso"
    source.write_text("input", encoding="utf-8")
    old = Compiler(output_dir=str(tmp_path / "old"), parsers={"lateiso": _parser("old")})

    class LateParser(ParserPlugin):
        name = "late-parser"
        supported_types = ["lateiso"]

        def execute(self, ctx):
            ctx.document = Document(
                source=ctx.source, file_type="lateiso", title="late",
                pages=1, blocks=[Block(type=BlockType.PARAGRAPH, content="late")],
            )
            ctx.document.compute_id()
            return ctx

    registry.register_parser("lateiso", LateParser)
    old_text, _ = old.compile_to_string(str(source))
    new_text, _ = Compiler(output_dir=str(tmp_path / "new")).compile_to_string(str(source))

    assert "old" in old_text and "late" not in old_text
    assert "late" in new_text


def test_string_compilation_does_not_construct_unused_exporters(monkeypatch, tmp_path: Path):
    class BrokenExporter(ExporterPlugin):
        name = "broken-optional-exporter"

        def __init__(self):
            raise RuntimeError("optional dependency unavailable")

        def execute(self, ctx):
            return ctx

    monkeypatch.setattr(registry, "_plugin_classes", [*registry._plugin_classes, BrokenExporter])
    source = tmp_path / "sample.md"
    source.write_text("# heading", encoding="utf-8")
    text, ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(source))
    assert "heading" in text
    assert ctx.validation.passed


def test_compilers_receive_distinct_stateful_stage_instances(monkeypatch):
    class StatefulCleaner(CleanerPlugin):
        name = "stateful-isolation-cleaner"
        instances = 0

        def __init__(self):
            type(self).instances += 1

        def execute(self, ctx):
            return ctx

    monkeypatch.setattr(registry, "_plugin_classes", [*registry._plugin_classes, StatefulCleaner])
    first = Compiler()
    second = Compiler()
    first_plugin = next(p for p in first._plugins(CleanerPlugin) if isinstance(p, StatefulCleaner))
    second_plugin = next(p for p in second._plugins(CleanerPlugin) if isinstance(p, StatefulCleaner))
    assert first_plugin is not second_plugin
    assert StatefulCleaner.instances == 2


def test_package_uses_compiler_exporter_snapshot(monkeypatch, tmp_path: Path):
    class LateExporter(ExporterPlugin):
        name = "late-package-exporter"

        def __init__(self):
            raise RuntimeError("late exporter must not leak into existing compiler")

        def execute(self, ctx):
            return ctx

    source = tmp_path / "sample.md"
    source.write_text("# heading", encoding="utf-8")
    compiler = Compiler(output_dir=str(tmp_path / "out"))
    monkeypatch.setattr(registry, "_plugin_classes", [*registry._plugin_classes, LateExporter])

    context = compiler.compile_package(str(source))

    assert context.document is not None
    assert not context.validation.errors


def test_corpus_uses_compiler_parser_snapshot_and_custom_extensions(tmp_path: Path):
    source_dir = tmp_path / "corpus"
    source_dir.mkdir()
    source = source_dir / "sample.customiso"
    source.write_text("input", encoding="utf-8")
    compiler = Compiler(
        output_dir=str(tmp_path / "out"),
        parsers={"customiso": _parser("custom")},
    )

    result = compiler.compile_corpus(str(source_dir))

    assert result.processed == 1
    assert result.failed == []
    assert result.chunks[0]["documents"][0]["file_type"] == "customiso"
