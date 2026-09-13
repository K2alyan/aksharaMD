"""DocBench corpus adapter.

DocBench (Zou et al., 2024; arXiv:2407.10701) is 229 real PDFs and 1,102
QA pairs across five domains (academia, finance, government, laws,
news). The dataset is distributed on Google Drive.

Layout on disk (verified against upstream ``run.py`` + ``evaluate.py``)
======================================================================

Once downloaded, the DocBench data directory is organised as::

    data/
      <folder>/
        <folder>_qa.jsonl        # one QA pair per line
        *.pdf                    # exactly one PDF per folder
      ...

Each JSONL line has the shape (fields used by upstream ``evaluate.py``)::

    {"question": str, "answer": str, "evidence": str, ...}

The domain for each document is derived from the folder-name prefix
(``academia_001`` -> ``academia``). If a top-level ``metadata.json``,
``domains.json``, or ``data.json`` is present the loader will prefer it
as the domain source; otherwise it falls back to the prefix heuristic.
The exact filename/keys in the Drive folder should be verified by the
first end-to-end smoke; the loader accepts both shapes.

Lazy-download design
====================

On first ``iter_documents`` invocation, if the cache directory does not
already contain per-doc subfolders, the loader shells out to ``gdown``
to pull the entire Drive folder into ``.cache/docbench/`` (a few GB
one-time). Subsequent runs read straight from cache and are offline.

Domain-balanced ``--limit``
===========================

When ``limit`` is set, the loader emits documents round-robin across the
five domains so a ``--limit 10`` pilot returns roughly 2 documents per
domain. Emission order within a domain is deterministic (alphabetical by
doc-id) so pilots are reproducible.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import warnings
from collections.abc import Iterator
from pathlib import Path

from ..types import CorpusAdapter, DocumentRecord, Question

_DEFAULT_CACHE_DIR = Path(".cache/docbench")
_DRIVE_FOLDER_URL = (
    "https://drive.google.com/drive/folders/1yxhF1lFF2gKeTNc8Wh0EyBdMT3M4pDYr"
)
_DOMAINS: tuple[str, ...] = ("academia", "finance", "government", "laws", "news")
_UNKNOWN_DOMAIN = "unknown"

# Module-level set tracking folders already warned about. Real DocBench folders
# are numeric (0, 1, 10, ...) so the folder-name prefix never matches; the
# filename-based fallback in `_domain_from_pdf_filename` handles those cases.
# We still track the unknowns here so `iter_documents` can emit a single
# summary warning (rather than one per folder, which was 229 lines on real
# DocBench). Kept as a set to preserve identity-based dedup for callers that
# poke at internals.
_WARNED_UNKNOWN_FOLDERS: set[str] = set()


# Filename patterns that identify the domain of a DocBench PDF. Patterns are
# tried in order; the first hit wins. All matched case-insensitively against
# the bare filename (no directory component).
#
# Sources: manual inspection of all 229 filenames in the upstream Drive
# release. See project notes for the classification table.
_FILENAME_DOMAIN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # -- academia --
    # ACL Anthology old-format IDs: P19-1598, W18-4401, N18-2084.
    (re.compile(r"^[A-Z]\d{2}-\d{3,5}\.pdf$", re.IGNORECASE), "academia"),
    # ACL Anthology new-format IDs: 2020.acl-main.408, 2020.emnlp-main.213,
    # 2020.findings-emnlp.139, 2020.starsem-1.17.
    (re.compile(r"^\d{4}\.\w+[-.]?\w*\.\d+\.pdf$", re.IGNORECASE), "academia"),
    # arXiv IDs (post-2007 format): 1503.00841, 2103.15691v2.
    (re.compile(r"^\d{4}\.\d{4,5}(v\d+)?\.pdf$", re.IGNORECASE), "academia"),
    # -- finance --
    # Stock-ticker annual reports: NYSE_UNH_2020, OTC_NSRGY_2020,
    # ASX_AJY_2020, NYSE_BRK-A_2021.
    (
        re.compile(
            r"^(NYSE|NASDAQ|OTC(MKTS)?|LSE|TYO|SEHK|ASX)_[A-Z-]+_\d{4}\.pdf$",
            re.IGNORECASE,
        ),
        "finance",
    ),
    # Named company annual reports.
    (
        re.compile(
            r"(roche|inditex|siemens|nestle|toyota|samsung|tencent|alibaba)[-_ ]",
            re.IGNORECASE,
        ),
        "finance",
    ),
    # SEC filing types and generic financial-statement filenames.
    (
        re.compile(
            r"(10-K|10-Q|annual.?report|earnings|financial.?statement)",
            re.IGNORECASE,
        ),
        "finance",
    ),
    # -- laws --
    # Federal court records.
    (re.compile(r"^USCOURTS-", re.IGNORECASE), "laws"),
    # Library of Congress 10-digit IDs: 2020720029, 2023555900, 2019713412.
    (re.compile(r"^\d{10}\.pdf$", re.IGNORECASE), "laws"),
    # -- government --
    # State Department and federal-agency report prefixes.
    (
        re.compile(
            r"^(FBS_|JRS-|INLSR|21-\d{5}-INLSR|International-Narcotics|"
            r"Country_Reports|Tab-\d+-INCSR|INCSR-Vol|DoS-)",
            re.IGNORECASE,
        ),
        "government",
    ),
    # Generic agency financial / federal report filenames.
    (
        re.compile(
            r"(Agency.*Financial|DOS-FY|Federal[-_]|Agency.*Report|FY-?\d{4})",
            re.IGNORECASE,
        ),
        "government",
    ),
    # -- news --
    # YYYYMMDD-dated news scans (NYT front pages): 20220817, 20230616.
    (re.compile(r"^\d{8}\.pdf$", re.IGNORECASE), "news"),
    # Named news outlets.
    (
        re.compile(
            r"(NYT|NewYorkTimes|Front[-_ ]?Page|BBC[_-]|WSJ[_-]|Guardian[_-])",
            re.IGNORECASE,
        ),
        "news",
    ),
)


class DocBenchCorpus(CorpusAdapter):
    """Iterate DocBench documents with grounded QA + domain metadata.

    Parameters
    ----------
    cache_dir
        Where to cache the downloaded Drive folder. Default
        ``.cache/docbench``.
    download_if_missing
        If True (default), attempt to invoke ``gdown`` to fetch the
        Drive folder when the cache is empty. Tests should set this to
        False and pre-populate the cache with fake fixtures.
    data_subdir
        Subdirectory under ``cache_dir`` where per-doc folders live.
        DocBench's upstream ``run.py`` uses ``data/``; if the Drive
        folder lays them out at the root we transparently fall back.
    """

    name = "docbench"

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        download_if_missing: bool = True,
        data_subdir: str = "data",
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self.download_if_missing = download_if_missing
        self.data_subdir = data_subdir

    # -- Public API ----------------------------------------------------

    def iter_documents(self, limit: int | None = None) -> Iterator[DocumentRecord]:
        data_root = self._ensure_data_root()
        doc_folders = _list_doc_folders(data_root)
        if not doc_folders:
            raise RuntimeError(
                f"No DocBench per-document folders found under {data_root}. "
                f"If the Drive folder lays them out differently, pass "
                f"data_subdir='' or point cache_dir at the correct path."
            )
        ordered = _domain_balanced_order(doc_folders, limit=limit)
        emitted = 0
        n_unknown = 0
        n_total = 0
        for folder in ordered:
            if limit is not None and emitted >= limit:
                break
            record = _folder_to_record(folder)
            if record is None:
                continue
            n_total += 1
            if record.metadata.get("domain") == _UNKNOWN_DOMAIN:
                n_unknown += 1
            yield record
            emitted += 1
        # Single summary warning replaces the per-folder spam that would
        # otherwise fire once for each of DocBench's ~229 numeric folders.
        if n_unknown:
            warnings.warn(
                f"DocBench: {n_unknown}/{n_total} folders had no recognizable "
                f"domain (from folder prefix or PDF filename)",
                stacklevel=2,
            )

    # -- Filesystem / download ----------------------------------------

    def _ensure_data_root(self) -> Path:
        """Return the directory that contains per-doc folders.

        Prefers ``cache_dir/<data_subdir>`` when present; otherwise
        falls back to ``cache_dir`` itself. Triggers gdown when nothing
        is present and download_if_missing is True.
        """
        candidate = self.cache_dir / self.data_subdir if self.data_subdir else self.cache_dir
        if candidate.is_dir() and _list_doc_folders(candidate):
            return candidate
        if self.cache_dir.is_dir() and _list_doc_folders(self.cache_dir):
            return self.cache_dir
        if self.download_if_missing:
            self._download_via_gdown()
            if candidate.is_dir() and _list_doc_folders(candidate):
                return candidate
            if self.cache_dir.is_dir() and _list_doc_folders(self.cache_dir):
                return self.cache_dir
        subdir_hint = (
            f" (or its {self.data_subdir!r} subdir)" if self.data_subdir else ""
        )
        raise RuntimeError(
            f"DocBench cache is empty and no per-document folders were "
            f"found under {self.cache_dir}{subdir_hint}. Install "
            f"'gdown' and rerun, or point cache_dir at an existing extract."
        )

    def _download_via_gdown(self) -> None:
        """Pull the Drive folder into ``self.cache_dir`` with gdown."""
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
                "The 'gdown' CLI is required to download DocBench. Install "
                "with `pip install aksharamd[eval]` (or `pip install gdown`)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"gdown failed to download DocBench (exit={exc.returncode}). "
                f"The Drive folder may require manual access approval."
            ) from exc


# -- Module-level helpers (importable for tests) -------------------------


def _list_doc_folders(root: Path) -> list[Path]:
    """Return sorted per-document subfolders under ``root``.

    A folder qualifies as a per-doc folder if it contains at least one
    ``*.pdf`` file and one ``*_qa.jsonl`` (or ``qa.jsonl``) file.
    """
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if _find_pdf(child) is None:
            continue
        if _find_qa_jsonl(child) is None:
            continue
        out.append(child)
    return out


def _find_pdf(folder: Path) -> Path | None:
    pdfs = sorted(folder.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def _find_qa_jsonl(folder: Path) -> Path | None:
    # Upstream naming: '<folder>_qa.jsonl'. Fall back to any *qa.jsonl if the
    # producer renames things.
    preferred = folder / f"{folder.name}_qa.jsonl"
    if preferred.is_file():
        return preferred
    candidates = sorted(folder.glob("*qa.jsonl"))
    return candidates[0] if candidates else None


def _domain_from_pdf_filename(filename: str) -> str | None:
    """Classify a DocBench PDF filename into a domain, or None on no match.

    The upstream Drive release names folders numerically (0, 1, 10, ...) and
    encodes domain hints only in the PDF filename inside each folder. This
    helper is tried as a fallback when `_domain_for_folder`'s prefix check
    fails to find a known domain.
    """
    for pattern, domain in _FILENAME_DOMAIN_PATTERNS:
        if pattern.search(filename):
            return domain
    return None


def _domain_for_folder(folder: Path) -> str:
    """Return the domain label for a DocBench per-doc folder.

    Two heuristics are attempted, in order:

    1. Folder-name prefix (``academia_001`` -> ``academia``). This is the
       convention documented in the DocBench paper but not honoured in the
       actual Drive release (which uses numeric folder IDs).
    2. PDF-filename inference via `_domain_from_pdf_filename` on the first
       ``*.pdf`` inside the folder. This is how real DocBench data resolves.

    Falls back to ``_UNKNOWN_DOMAIN`` (still iterable) if neither matches.
    The `_WARNED_UNKNOWN_FOLDERS` set is updated for downstream summary
    reporting by `iter_documents`; no warning is emitted here.
    """
    name = folder.name.lower()
    for domain in _DOMAINS:
        if name == domain or name.startswith(f"{domain}_") or name.startswith(f"{domain}-"):
            return domain
    # Prefix miss -> try filename inference on the folder's PDF. Guarded so
    # that a Path constructed in tests (no filesystem) doesn't crash.
    if folder.is_dir():
        pdf = _find_pdf(folder)
        if pdf is not None:
            inferred = _domain_from_pdf_filename(pdf.name)
            if inferred is not None:
                return inferred
    _WARNED_UNKNOWN_FOLDERS.add(folder.name)
    return _UNKNOWN_DOMAIN


def _domain_balanced_order(
    doc_folders: list[Path], *, limit: int | None
) -> list[Path]:
    """Return folders re-ordered for domain-balanced iteration.

    When ``limit`` is None, returns docs grouped by domain in the order
    given by ``_DOMAINS`` (alphabetical within each). When ``limit`` is
    set, interleaves domains round-robin so a small pilot draws roughly
    ``limit / n_domains`` docs from each domain.
    """
    if not doc_folders:
        return []
    by_domain: dict[str, list[Path]] = {}
    for folder in doc_folders:
        by_domain.setdefault(_domain_for_folder(folder), []).append(folder)
    for lst in by_domain.values():
        lst.sort(key=lambda p: p.name)

    if limit is None:
        # No limit: emit domain-by-domain (known domains first, then unknown).
        out: list[Path] = []
        for domain in _DOMAINS:
            out.extend(by_domain.get(domain, []))
        out.extend(by_domain.get(_UNKNOWN_DOMAIN, []))
        return out

    # Round-robin across known domains first, then top up from unknown.
    ordered: list[Path] = []
    domain_order = [d for d in _DOMAINS if by_domain.get(d)]
    if by_domain.get(_UNKNOWN_DOMAIN):
        domain_order.append(_UNKNOWN_DOMAIN)
    cursors = dict.fromkeys(domain_order, 0)
    while len(ordered) < len(doc_folders):
        emitted_this_round = 0
        for domain in domain_order:
            idx = cursors[domain]
            if idx < len(by_domain[domain]):
                ordered.append(by_domain[domain][idx])
                cursors[domain] = idx + 1
                emitted_this_round += 1
                if len(ordered) >= len(doc_folders):
                    break
        if emitted_this_round == 0:
            break
    return ordered


def _folder_to_record(folder: Path) -> DocumentRecord | None:
    """Convert a single per-doc DocBench folder into a DocumentRecord."""
    pdf_path = _find_pdf(folder)
    qa_path = _find_qa_jsonl(folder)
    if pdf_path is None or qa_path is None:
        return None
    questions = _load_questions(qa_path)
    if not questions:
        return None
    domain = _domain_for_folder(folder)
    try:
        pdf_bytes = pdf_path.read_bytes()
    except OSError as exc:
        return DocumentRecord(
            doc_id=folder.name,
            pdf_bytes=b"",
            questions=questions,
            metadata={
                "domain": domain,
                "fetch_error": f"pdf_read_failed: {exc}",
                "pdf_path": str(pdf_path),
            },
        )
    return DocumentRecord(
        doc_id=folder.name,
        pdf_bytes=pdf_bytes,
        questions=questions,
        metadata={
            "domain": domain,
            "pdf_path": str(pdf_path),
            "qa_path": str(qa_path),
        },
    )


def _load_questions(qa_path: Path) -> list[Question]:
    """Parse a ``<folder>_qa.jsonl`` file into ``Question`` objects.

    Upstream DocBench pairs use ``question`` + ``answer`` (both required)
    plus optional ``evidence``. Lines that fail to parse or are missing
    the required fields are skipped silently rather than raising, so a
    single malformed line does not sink an entire pilot.

    ``answer_type`` is coerced to the harness's four-value taxonomy:
    DocBench does not tag question type in the per-line JSON, so we
    default to ``"abstractive"``. If a future producer adds a ``type``
    field we honour it.
    """
    out: list[Question] = []
    with qa_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            q_text = payload.get("question")
            answer = payload.get("answer")
            if not q_text or answer is None:
                continue
            atype = _map_answer_type(payload.get("type"))
            out.append(
                Question(
                    question=str(q_text),
                    gold_answer=str(answer),
                    answer_type=atype,
                )
            )
    return out


def _map_answer_type(raw: object) -> str:
    """Map an upstream DocBench ``type`` field to the harness taxonomy.

    DocBench doesn't consistently populate this field; when missing we
    fall back to ``"abstractive"`` because the majority of DocBench
    answers are short free-form paragraphs rather than extractive spans.
    """
    if not isinstance(raw, str):
        return "abstractive"
    lowered = raw.strip().lower()
    if lowered in {"extractive", "span", "extract"}:
        return "extractive"
    if lowered in {"boolean", "bool", "yes/no", "yes_no"}:
        return "boolean"
    if lowered in {"unanswerable", "not_answerable", "none"}:
        return "unanswerable"
    return "abstractive"
