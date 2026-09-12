"""QASPER corpus adapter.

QASPER (Dasigi et al., 2021) is ~1,585 NLP arXiv papers with ~5,049
information-seeking QA pairs. Each QA pair is one of:

* extractive: answer is a span (list of spans) from the paper.
* abstractive: a short free-form answer that paraphrases evidence.
* boolean: yes/no.
* unanswerable: no evidence in the paper.

Loader design
-------------
This adapter bypasses HuggingFace ``datasets`` entirely and pulls the
raw JSON releases directly from AllenAI's permanent S3 tarballs:

* ``qasper-train-dev-v0.3.tgz`` (train + dev/validation)
* ``qasper-test-and-evaluator-v0.3.tgz`` (test)

Modern ``datasets>=4.0`` refuses to load the ``allenai/qasper`` HF hub
entry because it is a script-based dataset (``qasper.py``) and requires
``trust_remote_code=True`` to execute. Fetching the underlying JSONs
directly makes the loader independent of ``datasets`` version drift.

The per-annotator answer-flattening logic is ported from EleutherAI's
lm-evaluation-harness ``lm_eval/tasks/qasper/utils.py::_categorise_answer``
(MIT licensed):
https://github.com/EleutherAI/lm-evaluation-harness/blob/main/lm_eval/tasks/qasper/utils.py

Downloaded PDFs (from arxiv) and the QASPER tarball/JSON extractions
are cached under ``.cache/qasper/`` so re-runs are offline.
"""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..types import CorpusAdapter, DocumentRecord, Question

_ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"
_DEFAULT_CACHE_DIR = Path(".cache/qasper")
_ARXIV_SLEEP_SECONDS = 3.0  # be polite to arxiv.org; matches their published crawler guidance.

# AllenAI's permanent S3 URLs. QASPER v0.3 has been stable since 2022.
_QASPER_TRAIN_DEV_URL = (
    "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz"
)
_QASPER_TEST_URL = (
    "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-test-and-evaluator-v0.3.tgz"
)

# QASPER's HF card historically used the alias "validation" for the dev
# split, so we preserve it for callers that pass split="validation".
_SPLIT_TO_JSON_FILENAME: dict[str, str] = {
    "train": "qasper-train-v0.3.json",
    "validation": "qasper-dev-v0.3.json",
    "dev": "qasper-dev-v0.3.json",
    "test": "qasper-test-v0.3.json",
}

_SPLIT_TO_TARBALL: dict[str, tuple[str, str]] = {
    # split -> (url, tarball_filename)
    "train": (_QASPER_TRAIN_DEV_URL, "qasper-train-dev-v0.3.tgz"),
    "validation": (_QASPER_TRAIN_DEV_URL, "qasper-train-dev-v0.3.tgz"),
    "dev": (_QASPER_TRAIN_DEV_URL, "qasper-train-dev-v0.3.tgz"),
    "test": (_QASPER_TEST_URL, "qasper-test-and-evaluator-v0.3.tgz"),
}


class QasperCorpus(CorpusAdapter):
    """Iterate QASPER documents with grounded QA.

    Loads directly from AllenAI's permanent S3 tarballs; no ``datasets``
    dependency. Default split is ``"validation"`` (aka QASPER dev).
    """

    name = "qasper"

    def __init__(
        self,
        *,
        split: str = "validation",
        cache_dir: str | Path | None = None,
        questions_per_doc: int | None = None,
        arxiv_sleep_seconds: float = _ARXIV_SLEEP_SECONDS,
    ) -> None:
        if split not in _SPLIT_TO_JSON_FILENAME:
            raise ValueError(
                f"Unknown QASPER split: {split!r}. "
                f"Supported: {sorted(_SPLIT_TO_JSON_FILENAME)}"
            )
        self.split = split
        self.cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self.questions_per_doc = questions_per_doc
        self._arxiv_sleep_seconds = arxiv_sleep_seconds

    def iter_documents(self, limit: int | None = None) -> Iterator[DocumentRecord]:
        rows = self._load_split_rows()
        # Deterministic order by paper id so ``--limit N`` picks the same
        # N documents on every run and callers can reproduce results.
        emitted = 0
        for paper_id in sorted(rows):
            if limit is not None and emitted >= limit:
                return
            record = self._row_to_record(paper_id, rows[paper_id])
            if record is None:
                continue
            yield record
            emitted += 1

    # -- QASPER JSON loading ------------------------------------------

    def _load_split_rows(self) -> dict[str, dict[str, Any]]:
        """Return the QASPER split JSON as a dict keyed by paper id.

        Downloads and extracts the tarball on demand and caches results
        under ``self.cache_dir``. Idempotent: if the tarball is already
        present with a non-zero size the download is skipped, and the
        JSON is re-parsed straight from disk on every call.
        """
        json_filename = _SPLIT_TO_JSON_FILENAME[self.split]
        url, tarball_name = _SPLIT_TO_TARBALL[self.split]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tarball_path = self.cache_dir / tarball_name
        json_path = self.cache_dir / json_filename
        if not json_path.is_file() or json_path.stat().st_size == 0:
            if not tarball_path.is_file() or tarball_path.stat().st_size == 0:
                data = _download_bytes(url)
                tarball_path.write_bytes(data)
            _extract_qasper_tarball(tarball_path, self.cache_dir)
        with json_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"QASPER JSON {json_path} is not a dict keyed by paper id"
            )
        return payload

    def _row_to_record(
        self, paper_id: str, row: dict[str, Any]
    ) -> DocumentRecord | None:
        arxiv_id = str(paper_id).strip()
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
    """Convert a QASPER paper's ``qas`` structure to our Question shape.

    In the raw JSON release ``qas`` is a list of dicts, each shaped::

        {
            "question": str,
            "question_id": str,
            "answers": [
                {"answer": {"unanswerable": bool, "yes_no": bool|None,
                            "free_form_answer": str,
                            "extractive_spans": [str],
                            "evidence": [str]}}
            ]
        }

    For backwards compatibility with the HuggingFace column-projected
    shape (a dict with parallel ``question`` and ``answers`` arrays)
    the function also handles that layout — this keeps the existing
    unit tests, which use the HF shape, passing.
    """
    qas_field = row.get("qas")
    if isinstance(qas_field, list):
        # Raw JSON shape (list of {question, answers, ...}).
        out: list[Question] = []
        for entry in qas_field:
            if not isinstance(entry, dict):
                continue
            q_text = entry.get("question")
            answers = entry.get("answers") or []
            gold, answer_type = _canonical_answer_from_raw_answers(answers)
            out.append(
                Question(
                    question=str(q_text or ""),
                    gold_answer=gold,
                    answer_type=answer_type,
                )
            )
        return out
    if isinstance(qas_field, dict):
        # HuggingFace column-projected shape.
        questions_raw = qas_field.get("question")
        answers_raw = qas_field.get("answers")
        if not questions_raw:
            return []
        out = []
        for i, q_text in enumerate(questions_raw):
            answer_group = (
                answers_raw[i] if answers_raw and i < len(answers_raw) else None
            )
            gold, answer_type = _canonical_answer(answer_group)
            out.append(
                Question(
                    question=str(q_text),
                    gold_answer=gold,
                    answer_type=answer_type,
                )
            )
        return out
    return []


def _canonical_answer(answer_group: Any) -> tuple[str, str]:
    """Return (gold_answer_text, answer_type) from a QASPER answer group.

    Handles the HuggingFace column-projected shape where an answer_group
    is a dict with an ``answer`` list of per-annotator dicts. Delegates
    to ``_categorise_answer`` for the per-annotator flatten decision.
    """
    if not answer_group:
        return "", "unanswerable"
    annotator_answers: list[dict[str, Any]] = []
    if isinstance(answer_group, dict) and "answer" in answer_group:
        raw = answer_group.get("answer") or []
        if isinstance(raw, list):
            annotator_answers = [a for a in raw if isinstance(a, dict)]
    for ann in annotator_answers:
        answer, atype = _categorise_answer(ann)
        if atype != "unanswerable":
            return answer, atype
    return "", "unanswerable"


def _canonical_answer_from_raw_answers(
    answers: list[dict[str, Any]] | Any,
) -> tuple[str, str]:
    """Same as ``_canonical_answer`` but for the raw JSON per-paper shape.

    In the raw release ``answers`` is a list of ``{"answer": {...}}``
    dicts (one per annotator). We flatten with the same
    "prefer answerable" strategy.
    """
    if not isinstance(answers, list):
        return "", "unanswerable"
    for entry in answers:
        if not isinstance(entry, dict):
            continue
        ann = entry.get("answer")
        if not isinstance(ann, dict):
            continue
        answer, atype = _categorise_answer(ann)
        if atype != "unanswerable":
            return answer, atype
    return "", "unanswerable"


def _categorise_answer(answer: dict[str, Any]) -> tuple[str, str]:
    """Flatten one QASPER annotator ``answer`` blob into (text, type).

    Ported from EleutherAI's ``lm-evaluation-harness`` (MIT-licensed):
    https://github.com/EleutherAI/lm-evaluation-harness/blob/main/lm_eval/tasks/qasper/utils.py

    Priority order (matches upstream):

    1. ``unanswerable`` truthy       -> ("unanswerable", "unanswerable")
    2. ``yes_no`` is True            -> ("yes", "bool")
    3. ``free_form_answer`` truthy   -> (that string, "free form answer")
    4. ``extractive_spans`` truthy   -> (spans joined with " ; ", "extractive_spans")
    5. ``yes_no`` is False           -> ("no", "bool")

    We map the upstream type labels to this harness's canonical labels
    so downstream code can rely on the four-label taxonomy used
    everywhere else here:

        "unanswerable"          -> "unanswerable"
        "bool"                  -> "boolean"
        "free form answer"      -> "abstractive"
        "extractive_spans"      -> "extractive"

    When the input matches no priority we return ("", "unanswerable")
    so callers can keep iterating over annotators.
    """
    if answer.get("unanswerable"):
        return "unanswerable", "unanswerable"
    yes_no = answer.get("yes_no")
    if yes_no is True:
        return "Yes", "boolean"
    free = answer.get("free_form_answer")
    if free:
        return str(free), "abstractive"
    spans = answer.get("extractive_spans") or []
    if isinstance(spans, list) and spans:
        return " ; ".join(str(s) for s in spans), "extractive"
    if yes_no is False:
        return "No", "boolean"
    return "", "unanswerable"


def _extract_qasper_tarball(tarball_path: Path, dest_dir: Path) -> None:
    """Extract QASPER JSONs from ``tarball_path`` into ``dest_dir``.

    Only member paths that end with ``.json`` are extracted, and each
    is flattened to ``dest_dir/<basename>`` so we do not accidentally
    create nested archive directories. Members with absolute paths or
    ``..`` components are rejected as a defensive measure.
    """
    with tarfile.open(tarball_path, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = member.name
            # Reject unsafe paths.
            if name.startswith("/") or ".." in Path(name).parts:
                continue
            if not name.endswith(".json"):
                continue
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            out_path = dest_dir / Path(name).name
            with fobj as source, out_path.open("wb") as sink:
                sink.write(source.read())


def _download_bytes(url: str) -> bytes:
    """Fetch arbitrary bytes via urllib. Isolated so tests can monkeypatch.

    Enforces the ``PARSED_VS_RAW_DISABLE_NETWORK`` escape hatch that the
    arxiv fetcher already respects, so a single env var kills all
    network traffic from the harness.
    """
    from urllib.request import Request, urlopen

    if os.environ.get("PARSED_VS_RAW_DISABLE_NETWORK") == "1":
        raise RuntimeError("Network disabled by PARSED_VS_RAW_DISABLE_NETWORK=1")
    req = Request(url, headers={"User-Agent": "aksharamd-parsed-vs-raw/0.1"})
    with urlopen(req, timeout=60) as resp:  # noqa: S310  # nosec B310  # trusted hard-coded https URL
        return resp.read()


def _download_pdf(url: str) -> bytes:
    """Fetch a PDF via urllib. Isolated so tests can monkeypatch."""
    from urllib.request import Request, urlopen

    if os.environ.get("PARSED_VS_RAW_DISABLE_NETWORK") == "1":
        raise RuntimeError("Network disabled by PARSED_VS_RAW_DISABLE_NETWORK=1")
    req = Request(url, headers={"User-Agent": "aksharamd-parsed-vs-raw/0.1"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310  # nosec B310  # arxiv.org is a trusted hard-coded https URL
        return resp.read()
