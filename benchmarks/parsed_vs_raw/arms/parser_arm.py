"""Arms B/C: answer from a parser's markdown extraction of the source PDF.

Each parser adapter yields the markdown + a readiness score. The
readiness score is what we want to correlate against downstream answer
quality (Doc 2's proposal). We support:

* ``markitdown``: Microsoft's MarkItDown converter, wrapped as a
  ``ParserAdapter`` and routed through ``Compiler(parser_adapter=...)``.
* ``aksharamd``: our bundled Compiler.
* ``docling``: IBM's Docling (optional; skip cleanly if missing).

Cross-arm honesty
-----------------
Both the ``markitdown`` and ``aksharamd`` arms compute their readiness
score from the AksharaMD Compiler's own ``ctx.manifest.readiness_score``
- i.e. the exact same instrument. The MarkItDown arm gets there by
plugging a ``MarkItDownAdapter`` into ``Compiler(parser_adapter=...)``.
This is the whole point of PR #151's ParserAdapter boundary: swap the
parser, keep the scoring instrument identical.

The old verdict-derived proxy score (PASS=100, CONCERN=-15, FAIL=-30,
UNDETERMINED=-5) has been removed; comparing arms scored with different
instruments would poison the cross-arm correlation this harness is
designed to measure.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..llm_client import get_client, set_current_arm
from ..types import ArmResult, Question, judge_score_to_correctness


class ParserUnavailable(Exception):
    """Raised when an optional parser dependency is missing."""


@dataclass
class ExtractionOutput:
    markdown: str
    readiness_score: int | None
    parser_name: str
    parser_version: str | None = None
    error: str = ""


# -- extraction backends --------------------------------------------------


def _compile_pdf_bytes(pdf_bytes: bytes, *, parser_adapter=None):
    """Compile ``pdf_bytes`` through ``Compiler`` and return (markdown, ctx).

    Writes the PDF to a temp file (Compiler.compile_to_string takes a
    path) and cleans up afterwards. If ``parser_adapter`` is passed the
    compile call routes through the adapter boundary so downstream
    readiness scoring is done by the same instrument regardless of
    which parser produced the bytes.
    """
    from aksharamd.compiler import Compiler

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(pdf_bytes)
        tmp_path = fh.name
    try:
        compiler = Compiler(
            output_dir=tempfile.mkdtemp(prefix="pvr_compile_"),
            parser_adapter=parser_adapter,
        )
        markdown, ctx = compiler.compile_to_string(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return markdown, ctx


def _readiness_from_ctx(ctx) -> int | None:
    manifest = getattr(ctx, "manifest", None)
    if manifest is None:
        return None
    return getattr(manifest, "readiness_score", None)


def _extract_markitdown(pdf_bytes: bytes) -> ExtractionOutput:
    try:
        from ..adapters.markitdown_adapter import MarkItDownAdapter
    except ImportError as exc:  # pragma: no cover - defensive
        raise ParserUnavailable(f"markitdown adapter import failed: {exc}") from exc
    try:
        adapter = MarkItDownAdapter()
    except RuntimeError as exc:
        # MarkItDownAdapter.__post_init__ raises RuntimeError when the
        # markitdown package is not installed. Surface as ParserUnavailable
        # so the driver treats the arm as skippable rather than crashing.
        raise ParserUnavailable(str(exc)) from exc
    markdown, ctx = _compile_pdf_bytes(pdf_bytes, parser_adapter=adapter)
    return ExtractionOutput(
        markdown=markdown,
        readiness_score=_readiness_from_ctx(ctx),
        parser_name="markitdown",
    )


def _extract_aksharamd(pdf_bytes: bytes) -> ExtractionOutput:
    markdown, ctx = _compile_pdf_bytes(pdf_bytes)
    return ExtractionOutput(
        markdown=markdown,
        readiness_score=_readiness_from_ctx(ctx),
        parser_name="aksharamd",
    )


def _extract_docling(pdf_bytes: bytes) -> ExtractionOutput:
    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ParserUnavailable("docling not installed") from exc
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(pdf_bytes)
        tmp_path = fh.name
    try:
        conv = DocumentConverter()
        result = conv.convert(tmp_path)
        markdown = result.document.export_to_markdown()
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    # Docling still runs on the legacy path (raw output, no ParserAdapter
    # wrapper yet). Its readiness score comes from routing the markdown
    # through the Compiler with a synthetic PDF wrap; simpler and honest
    # to leave it as None for now than to mix instruments. Follow-up: add
    # a DoclingAdapter mirroring MarkItDownAdapter.
    return ExtractionOutput(
        markdown=markdown,
        readiness_score=None,
        parser_name="docling",
    )


_EXTRACTORS = {
    "markitdown": _extract_markitdown,
    "aksharamd": _extract_aksharamd,
    "docling": _extract_docling,
}


# -- arm runner -----------------------------------------------------------


@dataclass
class ParserArm:
    """Runs one parser arm against the active LLM client."""

    parser: str
    answer_model: str
    judge_model: str

    def extract(self, pdf_bytes: bytes) -> ExtractionOutput:
        try:
            extractor = _EXTRACTORS[self.parser]
        except KeyError as exc:
            raise ValueError(f"Unknown parser arm: {self.parser!r}") from exc
        return extractor(pdf_bytes)

    def run(self, doc_id: str, pdf_bytes: bytes, question: Question) -> ArmResult:
        set_current_arm(self.parser)
        try:
            extraction = self.extract(pdf_bytes)
        except ParserUnavailable as exc:
            return ArmResult(
                doc_id=doc_id,
                question=question.question,
                gold_answer=question.gold_answer,
                arm=self.parser,
                answer="",
                readiness_score=None,
                judge_score=-1,
                correctness=0.0,
                error=f"parser_unavailable: {exc}",
            )
        client = get_client()
        response = client.answer_from_markdown(
            question.question, extraction.markdown, model=self.answer_model
        )
        if response.error:
            return ArmResult(
                doc_id=doc_id,
                question=question.question,
                gold_answer=question.gold_answer,
                arm=self.parser,
                answer="",
                readiness_score=extraction.readiness_score,
                judge_score=-1,
                correctness=0.0,
                error=response.error,
            )
        judge_score = client.judge(
            question.question, question.gold_answer, response.text, model=self.judge_model
        )
        return ArmResult(
            doc_id=doc_id,
            question=question.question,
            gold_answer=question.gold_answer,
            arm=self.parser,
            answer=response.text,
            readiness_score=extraction.readiness_score,
            judge_score=judge_score,
            correctness=judge_score_to_correctness(judge_score),
        )


def run_parser_arm(
    *,
    doc_id: str,
    pdf_bytes: bytes,
    question: Question,
    parser: str,
    answer_model: str,
    judge_model: str,
) -> ArmResult:
    """Convenience function mirroring the raw-arm shape."""
    return ParserArm(parser=parser, answer_model=answer_model, judge_model=judge_model).run(
        doc_id, pdf_bytes, question
    )
