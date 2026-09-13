"""TAT-DQA corpus adapter.

TAT-DQA (Zhu et al., ACM MM 2022) is a document VQA dataset of 3,067
pages sampled from real SEC financial reports with 16,558 QA pairs.
Every document is a table+text hybrid, so no domain balancing is
required (single shape).

Layout on disk (verified against ``Doc2SoarGraph/etr/data/tat_dqa.py``)
======================================================================

The Drive distribution unpacks to::

    dataset_tatdqa/
      tatdqa_dataset_train.json
      tatdqa_dataset_dev.json
      tatdqa_dataset_test.json
      tat_docs/
        train/
          <uid>.json               # converted per-page layout JSON
          <uid>_1.png, ...         # rasterised page images
          <uid>.pdf                # original PDF (present in the public dump)
        dev/ ...
        test/ ...

The ``tatdqa_dataset_<split>.json`` file is a JSON *list* where each
element is::

    {
        "doc": {"uid": str, "page": int, "source": str, ...},
        "questions": [
            {
                "uid": str,
                "question": str,
                "answer": str | list[str] | number,
                "answer_type": "span"|"multi-span"|"count"|"arithmetic",
                "scale": str,
                "derivation": str,
                "facts": [...],
                "block_mapping": {...},
                "order": int
            },
            ...
        ]
    }

The original PDFs live alongside the layout JSONs under
``tat_docs/<split>/<uid>.pdf``. If a distribution ships the PDFs in a
different location the loader accepts a caller-supplied ``pdf_dir``.

Lazy-download design
====================

On first ``iter_documents`` invocation, if the cache directory is
missing the JSON manifest, the loader shells out to ``gdown`` to pull
the entire Drive folder (a few GB one-time). Subsequent runs are
offline.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..types import CorpusAdapter, DocumentRecord, Question

_DEFAULT_CACHE_DIR = Path(".cache/tat_dqa")
_DRIVE_FOLDER_URL = (
    "https://drive.google.com/drive/folders/1SGpZyRWqycMd_dZim1ygvWhl5KdJYDR2"
)

_SPLIT_TO_JSON = {
    "train": "tatdqa_dataset_train.json",
    "dev": "tatdqa_dataset_dev.json",
    "validation": "tatdqa_dataset_dev.json",
    "test": "tatdqa_dataset_test.json",
}
# Map upstream TAT-DQA answer_type onto the harness's four-value taxonomy.
# TAT-DQA has no boolean/unanswerable branches; everything answerable is
# either an extractive span or an abstractive/derived numeric answer.
_TATDQA_TYPE_MAP = {
    "span": "extractive",
    "multi-span": "extractive",
    "count": "abstractive",
    "arithmetic": "abstractive",
}


class TatDqaCorpus(CorpusAdapter):
    """Iterate TAT-DQA documents with grounded QA pairs.

    Parameters
    ----------
    split
        Which TAT-DQA split to iterate. ``"dev"`` is the default so
        pilots don't leak test-set answers into an LLM-judge prompt.
    cache_dir
        Where to cache the downloaded Drive folder. Default
        ``.cache/tat_dqa``.
    download_if_missing
        If True (default), invoke ``gdown`` when the cache lacks the
        JSON manifest. Tests should set this to False and pre-populate
        the cache with fixtures.
    pdf_subdir
        Subdirectory (relative to cache_dir) under which per-split PDF
        folders live. Defaults to ``tat_docs``.
    """

    name = "tat-dqa"

    def __init__(
        self,
        *,
        split: str = "dev",
        cache_dir: str | Path | None = None,
        download_if_missing: bool = True,
        pdf_subdir: str = "tat_docs",
    ) -> None:
        if split not in _SPLIT_TO_JSON:
            raise ValueError(
                f"Unknown TAT-DQA split: {split!r}. Supported: "
                f"{sorted(_SPLIT_TO_JSON)}"
            )
        self.split = split
        self.cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self.download_if_missing = download_if_missing
        self.pdf_subdir = pdf_subdir

    # -- Public API ----------------------------------------------------

    def iter_documents(self, limit: int | None = None) -> Iterator[DocumentRecord]:
        manifest_path = self._ensure_manifest()
        with manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise RuntimeError(
                f"TAT-DQA manifest {manifest_path} is not a JSON list; got "
                f"{type(payload).__name__}"
            )
        # Deterministic order by document uid so --limit N is reproducible.
        ordered = sorted(payload, key=lambda entry: _extract_uid(entry) or "")
        emitted = 0
        for entry in ordered:
            if limit is not None and emitted >= limit:
                return
            record = self._entry_to_record(entry)
            if record is None:
                continue
            yield record
            emitted += 1

    # -- Filesystem / download ----------------------------------------

    def _ensure_manifest(self) -> Path:
        """Return the path to the split JSON, downloading if needed."""
        json_filename = _SPLIT_TO_JSON[self.split]
        # The Drive folder may unpack into ``dataset_tatdqa/`` or dump the
        # JSONs straight into the cache root; accept either.
        candidates = [
            self.cache_dir / json_filename,
            self.cache_dir / "dataset_tatdqa" / json_filename,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        if self.download_if_missing:
            self._download_via_gdown()
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
        raise RuntimeError(
            f"TAT-DQA manifest {json_filename} not found under {self.cache_dir}. "
            f"Install 'gdown' and rerun, or place the manifest manually."
        )

    def _pdf_dir(self, manifest_path: Path) -> Path:
        """Locate the per-split PDF directory alongside the manifest."""
        # Prefer sibling-of-manifest layout (dataset_tatdqa/tat_docs/<split>/).
        return manifest_path.parent / self.pdf_subdir / self.split

    def _download_via_gdown(self) -> None:
        if os.environ.get("PARSED_VS_RAW_DISABLE_NETWORK") == "1":
            raise RuntimeError("Network disabled by PARSED_VS_RAW_DISABLE_NETWORK=1")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(  # noqa: S603  # nosec B603  # trusted argv, no shell
                [
                    "gdown",
                    "--folder",
                    _DRIVE_FOLDER_URL,
                    "-O",
                    str(self.cache_dir),
                ],
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "The 'gdown' CLI is required to download TAT-DQA. Install "
                "with `pip install aksharamd[eval]` (or `pip install gdown`)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"gdown failed to download TAT-DQA (exit={exc.returncode}). "
                f"The Drive folder may require manual access approval."
            ) from exc

    # -- Record construction ------------------------------------------

    def _entry_to_record(self, entry: dict[str, Any]) -> DocumentRecord | None:
        uid = _extract_uid(entry)
        if not uid:
            return None
        questions = _extract_questions(entry.get("questions") or [])
        if not questions:
            return None
        manifest_path = self._ensure_manifest()
        pdf_path = self._pdf_dir(manifest_path) / f"{uid}.pdf"
        if not pdf_path.is_file():
            return DocumentRecord(
                doc_id=uid,
                pdf_bytes=b"",
                questions=questions,
                metadata={
                    "split": self.split,
                    "fetch_error": f"pdf_missing: {pdf_path}",
                    "uid": uid,
                },
            )
        try:
            pdf_bytes = pdf_path.read_bytes()
        except OSError as exc:
            return DocumentRecord(
                doc_id=uid,
                pdf_bytes=b"",
                questions=questions,
                metadata={
                    "split": self.split,
                    "fetch_error": f"pdf_read_failed: {exc}",
                    "uid": uid,
                },
            )
        return DocumentRecord(
            doc_id=uid,
            pdf_bytes=pdf_bytes,
            questions=questions,
            metadata={
                "split": self.split,
                "uid": uid,
                "pdf_path": str(pdf_path),
                "source": str((entry.get("doc") or {}).get("source", "")),
            },
        )


# -- Module-level helpers (importable for tests) -------------------------


def _extract_uid(entry: dict[str, Any]) -> str | None:
    """Return the document uid from a TAT-DQA manifest entry."""
    doc = entry.get("doc")
    if isinstance(doc, dict):
        uid = doc.get("uid")
        if uid:
            return str(uid)
    # Some dumps put uid at the top level; be tolerant.
    uid = entry.get("uid")
    return str(uid) if uid else None


def _extract_questions(raw_questions: list[Any]) -> list[Question]:
    """Convert TAT-DQA question dicts into harness ``Question`` objects."""
    out: list[Question] = []
    for entry in raw_questions:
        if not isinstance(entry, dict):
            continue
        q_text = entry.get("question")
        answer = entry.get("answer")
        if not q_text or answer is None:
            continue
        atype = _TATDQA_TYPE_MAP.get(
            str(entry.get("answer_type") or "").strip().lower(),
            "abstractive",
        )
        out.append(
            Question(
                question=str(q_text).strip(),
                gold_answer=_normalise_answer(answer),
                answer_type=atype,
            )
        )
    return out


def _normalise_answer(raw: Any) -> str:
    """Coerce TAT-DQA's polymorphic answer field into a canonical string.

    ``answer`` may be a string, a number, or a list of strings (for
    ``multi-span``). We join lists with ``" ; "`` to mirror the
    convention used by the QASPER loader for extractive spans, keeping
    downstream scoring consistent.
    """
    if isinstance(raw, list):
        return " ; ".join(str(x).strip() for x in raw if x is not None and str(x).strip())
    return str(raw).strip()
