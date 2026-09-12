"""Thin wrappers around the Anthropic messages API used by all arms.

We intentionally do NOT import ``benchmarks.llm_qa_eval`` at module load
time. That file has 934 lines of import-time side effects (dotenv load,
stdout reconfigure) that we do not want to inherit whenever a harness
consumer imports this module. Instead we lazily import the specific
callables we need (``_call_claude`` and ``_judge``) inside functions.

Both callables can be replaced with a fixture client via
``set_fixture_client``. This is how ``--fixture-mode`` and the tests run
offline.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class LLMAnswer:
    """LLM response for one arm's answer call."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""


class LLMClient(Protocol):
    """Structural interface for the answer + judge callables.

    The real client hits Anthropic. The fixture client returns canned
    responses. Both must implement the same two methods so the arm
    runners can be oblivious to which is in use.
    """

    def answer_from_markdown(self, question: str, markdown: str, *, model: str) -> LLMAnswer: ...

    def answer_from_pdf_bytes(self, question: str, pdf_bytes: bytes, *, model: str) -> LLMAnswer: ...

    def judge(self, question: str, expected: str, answer: str, *, model: str) -> int: ...


_active_client: LLMClient | None = None


def set_client(client: LLMClient | None) -> None:
    """Install an alternate client. Passing ``None`` restores the default."""
    global _active_client
    _active_client = client


def get_client() -> LLMClient:
    """Return the active client, defaulting to the real Anthropic wrapper.

    We construct the default lazily so importing this module never
    triggers an anthropic import (or a missing-API-key check).
    """
    if _active_client is not None:
        return _active_client
    return _AnthropicClient()


# -- real client -----------------------------------------------------------


class _AnthropicClient:
    """Default client that goes through the real anthropic SDK."""

    def answer_from_markdown(self, question: str, markdown: str, *, model: str) -> LLMAnswer:
        prompt = _markdown_prompt(question, markdown)
        return _call_claude_text(prompt, model=model)

    def answer_from_pdf_bytes(self, question: str, pdf_bytes: bytes, *, model: str) -> LLMAnswer:
        # PDF ingestion uses a ``document`` content block with base64
        # source. See https://docs.anthropic.com/en/docs/build-with-claude/pdf-support
        try:
            import anthropic
        except ImportError as exc:
            return LLMAnswer(text="", error=f"anthropic not installed: {exc}")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return LLMAnswer(text="", error="ANTHROPIC_API_KEY not set")
        b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
        try:
            msg = anthropic.Anthropic().messages.create(
                model=model,
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": _raw_prompt(question)},
                        ],
                    }
                ],
            )
            return LLMAnswer(
                text=_first_text_block(msg),
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
            )
        except Exception as exc:
            return LLMAnswer(text="", error=str(exc))

    def judge(self, question: str, expected: str, answer: str, *, model: str) -> int:
        # Delegate to the existing judge in benchmarks/llm_qa_eval.py so
        # the two harnesses stay consistent on scoring semantics. The
        # judge lives on the module (not exported) so we import by name.
        from benchmarks import llm_qa_eval as legacy

        # legacy._judge ignores the passed model; we override by
        # monkeypatching the module constant only for this call.
        prev_judge_model = legacy._JUDGE_MODEL
        legacy._JUDGE_MODEL = model
        try:
            result = legacy._judge(question, expected, answer)
        finally:
            legacy._JUDGE_MODEL = prev_judge_model
        if result.error:
            return -1
        return int(result.score)


def _call_claude_text(prompt: str, *, model: str) -> LLMAnswer:
    """Text-only Anthropic call. Returns an ``LLMAnswer``."""
    try:
        import anthropic
    except ImportError as exc:
        return LLMAnswer(text="", error=f"anthropic not installed: {exc}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return LLMAnswer(text="", error="ANTHROPIC_API_KEY not set")
    try:
        msg = anthropic.Anthropic().messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMAnswer(
            text=_first_text_block(msg),
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )
    except Exception as exc:
        return LLMAnswer(text="", error=str(exc))


def _first_text_block(msg: Any) -> str:
    """Extract the first ``text`` block from an anthropic Message.

    Anthropic's response ``content`` is a union of TextBlock / ToolUseBlock;
    we only care about the text. Isolated here so mypy sees a single
    well-typed helper.
    """
    for block in getattr(msg, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            return str(text).strip()
    return ""


def _markdown_prompt(question: str, markdown: str) -> str:
    return (
        "Answer the question strictly from the provided document text. "
        "If the answer is not in the text, reply exactly: 'Unanswerable from provided text'.\n\n"
        f"=== DOCUMENT ===\n{markdown}\n=== END DOCUMENT ===\n\n"
        f"Question: {question}\nAnswer:"
    )


def _raw_prompt(question: str) -> str:
    return (
        "Answer the question strictly from the attached PDF. "
        "If the answer is not present, reply exactly: 'Unanswerable from provided text'.\n\n"
        f"Question: {question}\nAnswer:"
    )


# -- fixture client --------------------------------------------------------


@dataclass
class FixtureClient:
    """Deterministic client that returns canned responses from a JSON file.

    The fixture file is a mapping ``arm -> question -> {"answer": str,
    "judge": int}``. Missing keys fall back to a default entry keyed
    under ``"*"``. This is what the tests and ``--fixture-mode`` use.
    """

    responses: dict[str, Any]

    def _lookup(self, arm: str, question: str) -> dict[str, Any]:
        by_arm = self.responses.get(arm) or self.responses.get("*") or {}
        return by_arm.get(question) or by_arm.get("*") or {}

    def answer_from_markdown(self, question: str, markdown: str, *, model: str) -> LLMAnswer:
        return self._answer("markdown", question)

    def answer_from_pdf_bytes(self, question: str, pdf_bytes: bytes, *, model: str) -> LLMAnswer:
        return self._answer("pdf", question)

    def _answer(self, mode: str, question: str) -> LLMAnswer:
        # Fixture clients ignore mode; the fixture is arm-keyed already
        # via responses. The mode field is retained so an author can
        # differentiate arms sharing an arm-name if needed.
        _ = mode
        # We don't know the arm here; the arm runners call answer via
        # get_client() and the FixtureClient uses set_current_arm to
        # scope lookups.
        arm = _CURRENT_ARM.get("value", "*")
        entry = self._lookup(arm, question)
        return LLMAnswer(text=str(entry.get("answer", "")))

    def judge(self, question: str, expected: str, answer: str, *, model: str) -> int:
        arm = _CURRENT_ARM.get("value", "*")
        entry = self._lookup(arm, question)
        # Fall back to a deterministic scoring rule when the fixture does
        # not carry an explicit judge value: exact match = 10, empty = 0,
        # otherwise 5. Keeps tests concise.
        if "judge" in entry:
            return int(entry["judge"])
        if not answer.strip():
            return 0
        if answer.strip().lower() == str(expected).strip().lower():
            return 10
        return 5


_CURRENT_ARM: dict[str, str] = {"value": "*"}


def set_current_arm(arm: str) -> None:
    """Route the fixture client's lookups to the given arm name."""
    _CURRENT_ARM["value"] = arm


def load_fixture_client(path: str | Path) -> FixtureClient:
    with open(path, encoding="utf-8") as fh:
        return FixtureClient(responses=json.load(fh))


__all__ = [
    "FixtureClient",
    "LLMAnswer",
    "LLMClient",
    "get_client",
    "load_fixture_client",
    "set_client",
    "set_current_arm",
]
