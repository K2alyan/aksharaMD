from pathlib import Path

from aksharamd.compiler import Compiler
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.plugins import registry
from aksharamd.plugins.base import ParserPlugin


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
