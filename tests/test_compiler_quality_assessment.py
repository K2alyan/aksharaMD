import hashlib
import json

from aksharamd.compiler import Compiler


def test_compile_saves_source_bound_assessment_for_markdown(tmp_path):
    source = tmp_path / "invoice.md"
    source.write_text("# Invoice 42\n\nBalance: $18", encoding="utf-8")
    output = tmp_path / "out"

    ctx = Compiler(output_dir=str(output)).compile(str(source))

    report = json.loads((output / "quality_assessment.json").read_text(encoding="utf-8"))
    document_bytes = (output / "document.md").read_bytes()
    source_bytes = source.read_bytes()
    assert report["assessment"]["candidate_hash"] == hashlib.sha256(document_bytes).hexdigest()
    assert report["assessment"]["source_hash"] == hashlib.sha256(source_bytes).hexdigest()
    assert report["source"]["capture_id"] == ctx.capture_id
    assert report["source"]["logical_id"] == ctx.source_id
    assert report["candidate"]["logical_id"] == ctx.manifest.document_id
    assert report["candidate"]["original_source_hash"] == ctx.capture_id
    assert report["candidate"]["parser_name"] == "markdown_parser"
    assert report["assessment"]["disposition"] == "ACCEPT"


def test_compile_does_not_write_source_grounded_assessment_for_binary_input(tmp_path):
    source = tmp_path / "data.bin"
    source.write_bytes(b"not a supported source")

    # No parser exists, but the assertion documents that this narrow adapter
    # never writes an output-only assessment for a binary source.
    Compiler(output_dir=str(tmp_path / "out")).compile(str(source))

    assert not (tmp_path / "out" / "quality_assessment.json").exists()


def test_compile_marks_declared_text_preview_as_hold(tmp_path):
    source = tmp_path / "long.txt"
    source.write_text(("ordinary text " * 1000 + "\n\n") * 10, encoding="utf-8")
    output = tmp_path / "out"

    Compiler(output_dir=str(output)).compile(str(source))

    report = json.loads((output / "quality_assessment.json").read_text(encoding="utf-8"))
    assert report["candidate"]["original_source_hash"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["assessment"]["disposition"] == "HOLD"
    assert report["assessment"]["dimensions"]["conversion_fidelity"]["findings"][0]["code"] == "CANDIDATE_DECLARED_TRUNCATED"
