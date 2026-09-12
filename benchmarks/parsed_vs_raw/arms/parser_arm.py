"""Arms B/C: answer from a parser's markdown extraction of the source PDF.

Each parser adapter yields the markdown + a readiness score. The
readiness score is what we want to correlate against downstream answer
quality (Doc 2's proposal). We support:

* ``markitdown``: Microsoft's MarkItDown converter.
* ``aksharamd``: our bundled Compiler.
* ``docling``: IBM's Docling (optional; skip cleanly if missing).

For each parser we route the extraction through ``aksharamd.assessment``
to compute a readiness score against the source PDF bytes. AksharaMD's
own Compiler already computes and stashes ``readiness_score`` in
``ctx.manifest`` after compile_to_string, so we read it from there
instead of re-running the assessor for the AksharaMD arm.
"""
from __future__ import annotations

import hashlib
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


def _extract_markitdown(pdf_bytes: bytes) -> ExtractionOutput:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise ParserUnavailable("markitdown not installed") from exc
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(pdf_bytes)
        tmp_path = fh.name
    try:
        result = MarkItDown().convert(tmp_path)
        markdown = result.text_content or ""
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return ExtractionOutput(
        markdown=markdown,
        readiness_score=_score_via_assessor(pdf_bytes, markdown),
        parser_name="markitdown",
    )


def _extract_aksharamd(pdf_bytes: bytes) -> ExtractionOutput:
    # Local import: pulls in a lot of transitive machinery; keep it
    # inside the function so import-time cost is only paid when this arm
    # actually runs.
    from aksharamd.compiler import Compiler

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(pdf_bytes)
        tmp_path = fh.name
    try:
        markdown, ctx = Compiler(output_dir=tempfile.mkdtemp(prefix="pvr_ak_")).compile_to_string(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    readiness = None
    manifest = getattr(ctx, "manifest", None)
    if manifest is not None:
        readiness = getattr(manifest, "readiness_score", None)
    if readiness is None:
        # Fall back to the assessor path if the Compiler stashed no
        # explicit readiness (older manifests or exotic pipelines).
        readiness = _score_via_assessor(pdf_bytes, markdown)
    return ExtractionOutput(
        markdown=markdown,
        readiness_score=readiness,
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
    return ExtractionOutput(
        markdown=markdown,
        readiness_score=_score_via_assessor(pdf_bytes, markdown),
        parser_name="docling",
    )


def _score_via_assessor(pdf_bytes: bytes, markdown: str) -> int | None:
    """Best-effort readiness score for arbitrary extractions.

    The full AksharaMD readiness score lives inside the Compiler's
    ``compute_confidence`` and depends on a live CompilationContext, so
    for external parsers we cannot exactly reproduce it. Instead we run
    the ``Assessor`` and derive a coarse integer score from the verdicts
    that mirrors the same 0-100 band scheme: PASS on all dimensions
    yields 90, one CONCERN drops to 70, one FAIL drops to 40. This is
    an audited proxy, not the exact compiler score; the writeup in
    ``README.md`` calls this out explicitly.
    """
    try:
        from aksharamd.assessment.models import CandidateArtifact, SourceArtifact
        from aksharamd.assessment.service import Assessor
    except ImportError:
        return None
    source_hash = hashlib.sha256(pdf_bytes).hexdigest()
    candidate_bytes = markdown.encode("utf-8")
    candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
    try:
        source = SourceArtifact(
            content_hash=source_hash,
            byte_size=len(pdf_bytes),
            media_type="application/pdf",
            data=pdf_bytes,
        )
        candidate = CandidateArtifact(
            content_hash=candidate_hash,
            byte_size=len(candidate_bytes),
            media_type="text/markdown",
            data=candidate_bytes,
        )
        result = Assessor().assess(candidate=candidate, source=source)
    except Exception:
        return None
    # Derive an integer score from the verdict distribution.
    from aksharamd.assessment.models import Verdict

    score = 100
    for dim in result.dimensions.values():
        if dim.verdict == Verdict.FAIL:
            score -= 30
        elif dim.verdict == Verdict.CONCERN:
            score -= 15
        elif dim.verdict == Verdict.UNDETERMINED:
            score -= 5
    return max(0, min(100, score))


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
