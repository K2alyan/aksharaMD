"""Arm A: answer directly from the raw PDF bytes.

The Anthropic messages API accepts a ``document`` content block whose
source is a base64 encoding of the PDF. We use that path as the
"no extraction" baseline. There is no readiness score here because
there is no extraction step to score.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..llm_client import get_client, set_current_arm
from ..types import ArmResult, Question, judge_score_to_correctness


@dataclass
class RawArm:
    """Runs the raw-PDF arm against the active LLM client."""

    answer_model: str
    judge_model: str
    arm_name: str = "raw"

    def run(self, doc_id: str, pdf_bytes: bytes, question: Question) -> ArmResult:
        client = get_client()
        set_current_arm(self.arm_name)
        response = client.answer_from_pdf_bytes(question.question, pdf_bytes, model=self.answer_model)
        if response.error:
            return ArmResult(
                doc_id=doc_id,
                question=question.question,
                gold_answer=question.gold_answer,
                arm=self.arm_name,
                answer="",
                readiness_score=None,
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
            arm=self.arm_name,
            answer=response.text,
            readiness_score=None,
            judge_score=judge_score,
            correctness=judge_score_to_correctness(judge_score),
        )


def run_raw_arm(
    *,
    doc_id: str,
    pdf_bytes: bytes,
    question: Question,
    answer_model: str,
    judge_model: str,
) -> ArmResult:
    """Convenience function mirroring the parser-arm shape."""
    return RawArm(answer_model=answer_model, judge_model=judge_model).run(doc_id, pdf_bytes, question)
