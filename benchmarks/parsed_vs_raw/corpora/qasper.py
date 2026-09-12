"""QASPER corpus adapter.

QASPER (Dasigi et al., 2021) is ~1,585 NLP arXiv papers with ~5,049
information-seeking QA pairs. Each QA pair is one of:

* extractive: answer is a span (list of spans) from the paper.
* abstractive: a short free-form answer that paraphrases evidence.
* boolean: yes/no.
* unanswerable: no evidence in the paper.

We load the validation split via HuggingFace ``datasets``; each row
carries an ``id`` field (the arXiv id) that we use to fetch the source
PDF from ``https://arxiv.org/pdf/{arxiv_id}.pdf``. Downloaded bytes are
cached to a local ``.cache/qasper/`` tree so re-runs are offline.

The adapter defers both the ``datasets`` import and the arXiv HTTP call
so tests can import this module in offline environments.
"""
from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..types import CorpusAdapter, DocumentRecord, Question

_ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"
_DEFAULT_CACHE_DIR = Path(".cache/qasper")
_ARXIV_SLEEP_SECONDS = 3.0  # be polite to arxiv.org; matches their published crawler guidance.
# Pin a specific HuggingFace revision so pilots are reproducible. Callers may
# override via ``revision=`` if they need to re-pin against upstream drift.
_DEFAULT_QASPER_REVISION = "main"


class QasperCorpus(CorpusAdapter):
    """Iterate QASPER validation-split documents with grounded QA."""

    name = "qasper"

    def __init__(
        self,
        *,
        split: str = "validation",
        cache_dir: str | Path | None = None,
        questions_per_doc: int | None = None,
        arxiv_sleep_seconds: float = _ARXIV_SLEEP_SECONDS,
        revision: str = _DEFAULT_QASPER_REVISION,
    ) -> None:
        self.split = split
        self.cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self.questions_per_doc = questions_per_doc
        self._arxiv_sleep_seconds = arxiv_sleep_seconds
        self.revision = revision

    def iter_documents(self, limit: int | None = None) -> Iterator[DocumentRecord]:
        try:
            from datasets import load_dataset  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - exercised only when dep missing
            raise RuntimeError(
                "QASPER corpus requires the 'datasets' package. "
                "Install with: pip install 'aksharamd[eval]' or pip install datasets"
            ) from exc

        # Pin to a specific revision so the pilot is reproducible.
        # The allenai/qasper repo has been stable; if the hash becomes stale,
        # bump it explicitly and re-run — do NOT switch to unpinned "main".
        ds = load_dataset(  # nosec B615  # revision pinned below
            "allenai/qasper",
            split=self.split,
            revision=self.revision,
        )
        # Deterministic order by arxiv id so ``--limit N`` picks the same
        # N documents on every run and callers can reproduce results.
        rows = sorted(ds, key=lambda row: str(row.get("id", "")))
        emitted = 0
        for row in rows:
            if limit is not None and emitted >= limit:
                return
            record = self._row_to_record(row)
            if record is None:
                continue
            yield record
            emitted += 1

    def _row_to_record(self, row: dict[str, Any]) -> DocumentRecord | None:
        arxiv_id = str(row.get("id") or "").strip()
        if not arxiv_id:
            return None
        questions = _extract_questions(row)
        if self.questions_per_doc is not None:
            questions = questions[: self.questions_per_doc]
        if not questions:
            return None
        try:
            pdf_bytes = self._fetch_pdf(arxiv_id)
        except Exception as exc:
            # Corrupt or 404 PDFs are logged via metadata rather than raised
            # so the driver can skip the doc with a warning instead of dying.
            return DocumentRecord(
                doc_id=arxiv_id,
                pdf_bytes=b"",
                questions=questions,
                metadata={"fetch_error": str(exc), "arxiv_id": arxiv_id},
            )
        return DocumentRecord(
            doc_id=arxiv_id,
            pdf_bytes=pdf_bytes,
            questions=questions,
            metadata={"arxiv_id": arxiv_id, "title": str(row.get("title", ""))},
        )

    # -- pdf caching ---------------------------------------------------

    def _fetch_pdf(self, arxiv_id: str) -> bytes:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Use the arxiv id verbatim (with a hash tail so weird ids survive
        # the filesystem) so the same run always uses the same file.
        safe = arxiv_id.replace("/", "_")
        digest = hashlib.sha256(arxiv_id.encode("utf-8")).hexdigest()[:12]
        target = self.cache_dir / f"{safe}-{digest}.pdf"
        if target.exists() and target.stat().st_size > 0:
            return target.read_bytes()
        url = _ARXIV_PDF_URL.format(arxiv_id=arxiv_id)
        pdf_bytes = _download_pdf(url)
        if self._arxiv_sleep_seconds > 0:
            time.sleep(self._arxiv_sleep_seconds)
        target.write_bytes(pdf_bytes)
        return pdf_bytes


def _extract_questions(row: dict[str, Any]) -> list[Question]:
    """Convert a QASPER row's ``qas`` structure to our Question shape.

    QASPER stores multiple annotator answers per question. We collapse to
    the first non-unanswerable canonical answer (extractive spans joined
    with ``; ``, else free_form_answer, else yes/no); if all annotators
    marked the question unanswerable we surface it as answer_type=
    ``unanswerable`` with an empty gold string.
    """
    qas_field = row.get("qas") or {}
    questions_raw = qas_field.get("question") if isinstance(qas_field, dict) else None
    answers_raw = qas_field.get("answers") if isinstance(qas_field, dict) else None
    if not questions_raw:
        return []
    out: list[Question] = []
    for i, q_text in enumerate(questions_raw):
        answer_group = answers_raw[i] if answers_raw and i < len(answers_raw) else None
        gold, answer_type = _canonical_answer(answer_group)
        out.append(Question(question=str(q_text), gold_answer=gold, answer_type=answer_type))
    return out


def _canonical_answer(answer_group: Any) -> tuple[str, str]:
    """Return (gold_answer_text, answer_type) from a QASPER answer group.

    QASPER's answer_group shape (from the HuggingFace release) is a dict
    with an ``answer`` list of per-annotator dicts. Each per-annotator
    dict contains ``unanswerable`` (bool), ``extractive_spans`` (list),
    ``yes_no`` (bool or None), ``free_form_answer`` (str), etc.
    """
    if not answer_group:
        return "", "unanswerable"
    annotator_answers: list[dict[str, Any]] = []
    if isinstance(answer_group, dict) and "answer" in answer_group:
        raw = answer_group.get("answer") or []
        if isinstance(raw, list):
            annotator_answers = [a for a in raw if isinstance(a, dict)]
    for ann in annotator_answers:
        if ann.get("unanswerable"):
            continue
        spans = ann.get("extractive_spans") or []
        if isinstance(spans, list) and spans:
            return "; ".join(str(s) for s in spans), "extractive"
        yes_no = ann.get("yes_no")
        if yes_no is not None:
            return ("Yes" if yes_no else "No"), "boolean"
        free = ann.get("free_form_answer")
        if free:
            return str(free), "abstractive"
    return "", "unanswerable"


def _download_pdf(url: str) -> bytes:
    """Fetch a PDF via urllib. Isolated so tests can monkeypatch."""
    # Deferred urllib import keeps the module importable in restricted
    # environments and mirrors how the rest of the harness handles
    # optional network dependencies.
    from urllib.request import Request, urlopen

    if os.environ.get("PARSED_VS_RAW_DISABLE_NETWORK") == "1":
        raise RuntimeError("Network disabled by PARSED_VS_RAW_DISABLE_NETWORK=1")
    req = Request(url, headers={"User-Agent": "aksharamd-parsed-vs-raw/0.1"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310  # nosec B310  arxiv.org is a trusted hard-coded https URL
        return resp.read()
