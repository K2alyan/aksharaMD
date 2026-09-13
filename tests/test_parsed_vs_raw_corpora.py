"""Tests for the DocBench + TAT-DQA corpus adapters.

These run entirely offline. No gdown call is ever issued; the tests
either construct a fake on-disk cache and set ``download_if_missing=False``
or pre-populate the exact files the loader expects to see.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from benchmarks.parsed_vs_raw.corpora import (
    DocBenchCorpus,
    TatDqaCorpus,
    get_corpus,
)
from benchmarks.parsed_vs_raw.corpora.docbench import (
    _domain_balanced_order,
    _domain_for_folder,
)
from benchmarks.parsed_vs_raw.types import DocumentRecord


# -- Shared tiny-PDF helper ---------------------------------------------


def _tiny_pdf() -> bytes:
    """Generate a synthetic single-page PDF for fixture use."""
    import fitz  # pymupdf

    doc = fitz.open()
    doc.new_page(width=72, height=72)
    data = doc.tobytes()
    doc.close()
    return data


# -- DocBench: smoke ------------------------------------------------------


def _make_docbench_folder(
    root: Path, folder_name: str, qa_pairs: list[dict[str, str]]
) -> Path:
    """Create a fake DocBench per-doc folder on disk."""
    folder = root / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{folder_name}.pdf").write_bytes(_tiny_pdf())
    qa_lines = "\n".join(json.dumps(q) for q in qa_pairs)
    (folder / f"{folder_name}_qa.jsonl").write_text(qa_lines + "\n", encoding="utf-8")
    return folder


def test_docbench_loader_smoke(tmp_path: Path) -> None:
    """Three fake docs across three domains resolve to three DocumentRecords."""
    data_root = tmp_path / "data"
    _make_docbench_folder(
        data_root,
        "academia_001",
        [
            {"question": "What is the main claim?", "answer": "SGD converges", "evidence": "sec 3"},
            {"question": "Was BERT used?", "answer": "Yes", "evidence": "sec 4"},
        ],
    )
    _make_docbench_folder(
        data_root,
        "finance_012",
        [
            {"question": "What was Q3 revenue?", "answer": "$4.2B", "evidence": "table 2"},
        ],
    )
    _make_docbench_folder(
        data_root,
        "news_007",
        [
            {"question": "Who was elected?", "answer": "Jane Doe", "evidence": "para 1"},
        ],
    )

    corpus = DocBenchCorpus(
        cache_dir=tmp_path,
        download_if_missing=False,
        data_subdir="data",
    )
    docs = list(corpus.iter_documents())

    assert len(docs) == 3
    doc_ids = {d.doc_id for d in docs}
    assert doc_ids == {"academia_001", "finance_012", "news_007"}

    academia = next(d for d in docs if d.doc_id == "academia_001")
    assert isinstance(academia, DocumentRecord)
    assert academia.metadata["domain"] == "academia"
    assert academia.pdf_bytes  # non-empty
    assert len(academia.questions) == 2
    assert academia.questions[0].question == "What is the main claim?"
    assert academia.questions[0].gold_answer == "SGD converges"
    # DocBench per-line JSON has no explicit type; loader defaults to abstractive.
    assert academia.questions[0].answer_type == "abstractive"

    finance = next(d for d in docs if d.doc_id == "finance_012")
    assert finance.metadata["domain"] == "finance"

    news = next(d for d in docs if d.doc_id == "news_007")
    assert news.metadata["domain"] == "news"


def test_docbench_loader_respects_explicit_type_field(tmp_path: Path) -> None:
    """When the upstream JSON carries a ``type`` field the loader honours it."""
    data_root = tmp_path / "data"
    _make_docbench_folder(
        data_root,
        "government_003",
        [
            {"question": "Is regulation X in effect?", "answer": "No", "type": "boolean"},
            {"question": "Cite section 5.", "answer": "Text of section 5", "type": "extractive"},
        ],
    )
    corpus = DocBenchCorpus(
        cache_dir=tmp_path,
        download_if_missing=False,
        data_subdir="data",
    )
    (doc,) = list(corpus.iter_documents())
    assert doc.metadata["domain"] == "government"
    types = [q.answer_type for q in doc.questions]
    assert types == ["boolean", "extractive"]


def test_docbench_domain_balanced_limit(tmp_path: Path) -> None:
    """``--limit`` produces a round-robin draw across all five domains."""
    data_root = tmp_path / "data"
    # Three docs per domain (15 total).
    for domain in ("academia", "finance", "government", "laws", "news"):
        for idx in range(3):
            _make_docbench_folder(
                data_root,
                f"{domain}_{idx:03d}",
                [{"question": f"q from {domain} #{idx}?", "answer": "a"}],
            )

    corpus = DocBenchCorpus(
        cache_dir=tmp_path,
        download_if_missing=False,
        data_subdir="data",
    )

    limit_5 = list(corpus.iter_documents(limit=5))
    assert len(limit_5) == 5
    domains_5 = Counter(d.metadata["domain"] for d in limit_5)
    # Exactly one per domain when limit == n_domains.
    assert set(domains_5) == {"academia", "finance", "government", "laws", "news"}
    assert all(count == 1 for count in domains_5.values())

    limit_10 = list(corpus.iter_documents(limit=10))
    assert len(limit_10) == 10
    domains_10 = Counter(d.metadata["domain"] for d in limit_10)
    assert set(domains_10) == {"academia", "finance", "government", "laws", "news"}
    assert all(count == 2 for count in domains_10.values())


def test_docbench_domain_for_folder_extracts_prefix() -> None:
    """Domain prefix heuristic handles both underscore and hyphen forms."""
    assert _domain_for_folder(Path("academia_001")) == "academia"
    assert _domain_for_folder(Path("finance-042")) == "finance"
    assert _domain_for_folder(Path("laws_xyz")) == "laws"
    # Unknown prefix -> "unknown" (still iterable).
    assert _domain_for_folder(Path("misc_zzz")) == "unknown"


def test_docbench_domain_balanced_order_no_limit_groups_by_domain(tmp_path: Path) -> None:
    """Without a limit the loader emits domain-by-domain (deterministic)."""
    folders = [
        Path("news_001"),
        Path("academia_003"),
        Path("finance_002"),
        Path("academia_001"),
        Path("laws_001"),
        Path("government_001"),
    ]
    ordered = _domain_balanced_order(folders, limit=None)
    # Order = _DOMAINS order (academia, finance, government, laws, news),
    # alphabetical within each.
    assert [p.name for p in ordered] == [
        "academia_001",
        "academia_003",
        "finance_002",
        "government_001",
        "laws_001",
        "news_001",
    ]


def test_docbench_iter_raises_without_gdown_when_cache_empty(tmp_path: Path) -> None:
    """Empty cache + download_if_missing=False must raise a clear error."""
    corpus = DocBenchCorpus(
        cache_dir=tmp_path,
        download_if_missing=False,
        data_subdir="data",
    )
    with pytest.raises(RuntimeError, match="DocBench cache is empty"):
        list(corpus.iter_documents())


# -- TAT-DQA: smoke ------------------------------------------------------


def _write_tatdqa_manifest(
    cache_dir: Path, split: str, entries: list[dict]
) -> Path:
    """Materialise a fake TAT-DQA manifest JSON on disk."""
    manifest_dir = cache_dir / "dataset_tatdqa"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    filename = {
        "train": "tatdqa_dataset_train.json",
        "dev": "tatdqa_dataset_dev.json",
        "validation": "tatdqa_dataset_dev.json",
        "test": "tatdqa_dataset_test.json",
    }[split]
    path = manifest_dir / filename
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _write_tatdqa_pdf(cache_dir: Path, split: str, uid: str) -> Path:
    pdf_dir = cache_dir / "dataset_tatdqa" / "tat_docs" / split
    pdf_dir.mkdir(parents=True, exist_ok=True)
    path = pdf_dir / f"{uid}.pdf"
    path.write_bytes(_tiny_pdf())
    return path


def test_tat_dqa_loader_smoke(tmp_path: Path) -> None:
    """Fake dev manifest + PDFs resolve to DocumentRecords with mapped types."""
    cache_dir = tmp_path / "tat_dqa"
    _write_tatdqa_manifest(
        cache_dir,
        "dev",
        [
            {
                "doc": {"uid": "abc123", "page": 1, "source": "10-K"},
                "questions": [
                    {
                        "uid": "q-1",
                        "question": "What was total revenue?",
                        "answer": "1,234",
                        "answer_type": "span",
                        "scale": "million",
                        "derivation": "",
                        "facts": [],
                        "block_mapping": {},
                        "order": 0,
                    },
                    {
                        "uid": "q-2",
                        "question": "Compute revenue growth.",
                        "answer": ["1234", "5.5"],
                        "answer_type": "arithmetic",
                        "scale": "percent",
                        "derivation": "growth",
                        "facts": [],
                        "block_mapping": {},
                        "order": 1,
                    },
                ],
            },
            {
                "doc": {"uid": "def456", "page": 2, "source": "10-Q"},
                "questions": [
                    {
                        "uid": "q-3",
                        "question": "List reporting segments.",
                        "answer": ["Consumer", "Enterprise"],
                        "answer_type": "multi-span",
                        "scale": "",
                        "derivation": "",
                        "facts": [],
                        "block_mapping": {},
                        "order": 0,
                    },
                ],
            },
        ],
    )
    _write_tatdqa_pdf(cache_dir, "dev", "abc123")
    _write_tatdqa_pdf(cache_dir, "dev", "def456")

    corpus = TatDqaCorpus(
        split="dev",
        cache_dir=cache_dir,
        download_if_missing=False,
    )
    docs = list(corpus.iter_documents())

    assert [d.doc_id for d in docs] == ["abc123", "def456"]
    first = docs[0]
    assert first.pdf_bytes  # non-empty
    assert first.metadata["split"] == "dev"
    assert first.metadata["uid"] == "abc123"
    assert first.metadata["source"] == "10-K"
    assert len(first.questions) == 2
    assert first.questions[0].question == "What was total revenue?"
    assert first.questions[0].gold_answer == "1,234"
    assert first.questions[0].answer_type == "extractive"  # 'span' -> extractive
    # 'arithmetic' maps to abstractive; list-typed answer is joined with ' ; '
    assert first.questions[1].answer_type == "abstractive"
    assert first.questions[1].gold_answer == "1234 ; 5.5"

    second = docs[1]
    assert second.questions[0].answer_type == "extractive"  # 'multi-span'
    assert second.questions[0].gold_answer == "Consumer ; Enterprise"


def test_tat_dqa_respects_limit(tmp_path: Path) -> None:
    """``--limit`` yields the requested number of docs in deterministic order."""
    cache_dir = tmp_path / "tat_dqa"
    entries = []
    for uid in ("uid-c", "uid-a", "uid-b"):
        entries.append(
            {
                "doc": {"uid": uid, "page": 1, "source": "10-K"},
                "questions": [
                    {
                        "uid": f"{uid}-q",
                        "question": "?",
                        "answer": "x",
                        "answer_type": "span",
                        "scale": "",
                    }
                ],
            }
        )
        _write_tatdqa_pdf(cache_dir, "dev", uid)
    _write_tatdqa_manifest(cache_dir, "dev", entries)

    corpus = TatDqaCorpus(
        split="dev",
        cache_dir=cache_dir,
        download_if_missing=False,
    )
    docs = list(corpus.iter_documents(limit=2))
    # Sorted alphabetically by uid, so first two are uid-a, uid-b.
    assert [d.doc_id for d in docs] == ["uid-a", "uid-b"]


def test_tat_dqa_missing_pdf_records_fetch_error(tmp_path: Path) -> None:
    """A missing PDF is surfaced via metadata['fetch_error'], not an exception."""
    cache_dir = tmp_path / "tat_dqa"
    _write_tatdqa_manifest(
        cache_dir,
        "dev",
        [
            {
                "doc": {"uid": "no_pdf", "page": 1, "source": "10-K"},
                "questions": [
                    {
                        "uid": "q-x",
                        "question": "?",
                        "answer": "x",
                        "answer_type": "span",
                    }
                ],
            }
        ],
    )
    # deliberately do NOT write a PDF file
    corpus = TatDqaCorpus(
        split="dev",
        cache_dir=cache_dir,
        download_if_missing=False,
    )
    (doc,) = list(corpus.iter_documents())
    assert doc.pdf_bytes == b""
    assert "pdf_missing" in doc.metadata.get("fetch_error", "")


def test_tat_dqa_rejects_unknown_split() -> None:
    with pytest.raises(ValueError):
        TatDqaCorpus(split="bogus")


# -- Corpus registry -----------------------------------------------------


def test_corpus_registry_docbench_and_tat_dqa() -> None:
    """``get_corpus`` returns a callable adapter for both new corpora.

    We intentionally do NOT call ``iter_documents`` here — that would
    trigger a gdown fetch. Just prove import + construction succeed and
    the returned object exposes the CorpusAdapter iteration API.
    """
    docbench = get_corpus("docbench")
    assert isinstance(docbench, DocBenchCorpus)
    assert docbench.name == "docbench"
    assert hasattr(docbench, "iter_documents") and callable(docbench.iter_documents)

    tat = get_corpus("tat-dqa")
    assert isinstance(tat, TatDqaCorpus)
    assert tat.name == "tat-dqa"
    assert hasattr(tat, "iter_documents") and callable(tat.iter_documents)

    # Aliases: ``tat_dqa`` and ``tatdqa`` should also resolve.
    assert isinstance(get_corpus("tat_dqa"), TatDqaCorpus)
    assert isinstance(get_corpus("tatdqa"), TatDqaCorpus)


def test_corpus_registry_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown corpus"):
        get_corpus("no-such-thing")
