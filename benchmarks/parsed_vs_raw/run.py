"""Driver + click CLI for the parsed-vs-raw evaluation harness.

Typical invocation:

    python -m benchmarks.parsed_vs_raw.run \\
        --corpus qasper --limit 5 --questions-per-doc 3 \\
        --arms raw,markitdown,aksharamd \\
        --answer-model claude-haiku-4-5-20251001 \\
        --judge-model claude-haiku-4-5-20251001 \\
        --output benchmarks/results/parsed-vs-raw-qasper-pilot/

``--dry-run`` produces per-arm extractions on disk without calling any
LLM; useful for auditing the pipeline before spending API dollars.

``--fixture-mode`` uses a JSON fixture of canned LLM responses so the
whole thing runs offline (this is what the test suite exercises).
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import click

from .aggregate import summarise, write_rows_csv, write_summary_markdown
from .arms.parser_arm import ParserArm
from .arms.raw_arm import RawArm
from .corpora import get_corpus
from .llm_client import load_fixture_client, set_client
from .types import ArmResult, DocumentRecord

_ALL_ARMS = ("raw", "markitdown", "aksharamd", "docling")


def _parse_arms(raw: str) -> list[str]:
    arms = [a.strip().lower() for a in raw.split(",") if a.strip()]
    unknown = [a for a in arms if a not in _ALL_ARMS]
    if unknown:
        raise click.BadParameter(f"Unknown arm(s): {unknown}. Choose from {_ALL_ARMS}.")
    return arms


def _iter_documents(corpus_name: str, limit: int, questions_per_doc: int) -> list[DocumentRecord]:
    """Load documents up-front so failures show early, before any LLM call."""
    corpus = get_corpus(corpus_name)
    # QasperCorpus supports questions_per_doc as a constructor arg; other
    # adapters may not. We prefer to trim here so all corpora share one
    # code path.
    docs = list(corpus.iter_documents(limit=limit))
    trimmed: list[DocumentRecord] = []
    for doc in docs:
        if questions_per_doc:
            doc.questions = doc.questions[:questions_per_doc]
        trimmed.append(doc)
    return trimmed


def _run_arms(
    docs: Sequence[DocumentRecord],
    arms: Sequence[str],
    *,
    answer_model: str,
    judge_model: str,
    dry_run: bool,
    output_dir: Path,
) -> list[ArmResult]:
    results: list[ArmResult] = []
    for doc in docs:
        if not doc.pdf_bytes:
            click.echo(f"[skip] {doc.doc_id}: no PDF bytes ({doc.metadata.get('fetch_error', 'unknown')})", err=True)
            continue
        for arm_name in arms:
            for question in doc.questions:
                if dry_run:
                    results.append(_dry_run_row(doc, question, arm_name, output_dir))
                    continue
                if arm_name == "raw":
                    raw_runner = RawArm(answer_model=answer_model, judge_model=judge_model)
                    results.append(raw_runner.run(doc.doc_id, doc.pdf_bytes, question))
                else:
                    parser_runner = ParserArm(
                        parser=arm_name, answer_model=answer_model, judge_model=judge_model
                    )
                    results.append(parser_runner.run(doc.doc_id, doc.pdf_bytes, question))
    return results


def _dry_run_row(
    doc: DocumentRecord, question, arm_name: str, output_dir: Path
) -> ArmResult:
    """Perform extraction (if applicable) and write it to disk; skip LLM calls."""
    from .arms.parser_arm import ParserArm, ParserUnavailable
    from .types import ArmResult

    readiness = None
    error = ""
    if arm_name != "raw":
        try:
            extraction = ParserArm(
                parser=arm_name,
                answer_model="",
                judge_model="",
            ).extract(doc.pdf_bytes)
            readiness = extraction.readiness_score
            (output_dir / "dry_run" / arm_name).mkdir(parents=True, exist_ok=True)
            (output_dir / "dry_run" / arm_name / f"{doc.doc_id}.md").write_text(
                extraction.markdown, encoding="utf-8"
            )
        except ParserUnavailable as exc:
            error = f"parser_unavailable: {exc}"
    return ArmResult(
        doc_id=doc.doc_id,
        question=question.question,
        gold_answer=question.gold_answer,
        arm=arm_name,
        answer="[dry-run]",
        readiness_score=readiness,
        judge_score=-1,
        correctness=0.0,
        error=error,
    )


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--corpus", default="qasper", show_default=True, help="Corpus adapter to load.")
@click.option("--limit", type=int, default=5, show_default=True, help="Max documents to load.")
@click.option(
    "--questions-per-doc",
    type=int,
    default=3,
    show_default=True,
    help="Cap questions per document. 0 = no cap.",
)
@click.option(
    "--arms",
    "arms_str",
    default="raw,markitdown,aksharamd",
    show_default=True,
    help="Comma-separated arms.",
)
@click.option(
    "--answer-model",
    default="claude-haiku-4-5-20251001",
    show_default=True,
    help="Anthropic model for answer generation.",
)
@click.option(
    "--judge-model",
    default="claude-haiku-4-5-20251001",
    show_default=True,
    help="Anthropic model for LLM-as-judge scoring.",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Directory that receives rows.csv, summary.md, and dry-run artifacts.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Produce extractions + readiness scores; skip all LLM calls.",
)
@click.option(
    "--fixture-mode",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a JSON fixture of canned LLM responses (bypasses the real API).",
)
def main(
    corpus: str,
    limit: int,
    questions_per_doc: int,
    arms_str: str,
    answer_model: str,
    judge_model: str,
    output_dir: Path,
    dry_run: bool,
    fixture_mode: Path | None,
) -> None:
    """Run the parsed-vs-raw evaluation pilot."""
    arms = _parse_arms(arms_str)
    if not arms:
        raise click.BadParameter("At least one arm must be specified.")
    if fixture_mode is not None:
        set_client(load_fixture_client(fixture_mode))
    try:
        docs = _iter_documents(corpus, limit=limit, questions_per_doc=questions_per_doc)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    if not docs:
        raise click.ClickException("No documents loaded; corpus was empty.")
    output_dir.mkdir(parents=True, exist_ok=True)
    results = _run_arms(
        docs,
        arms,
        answer_model=answer_model,
        judge_model=judge_model,
        dry_run=dry_run,
        output_dir=output_dir,
    )
    write_rows_csv(results, output_dir / "rows.csv")
    summaries = summarise(results)
    write_summary_markdown(summaries, output_dir / "summary.md")
    (output_dir / "summary.json").write_text(
        json.dumps([asdict(s) for s in summaries], indent=2),
        encoding="utf-8",
    )
    click.echo(f"Wrote {len(results)} rows to {output_dir/'rows.csv'}")
    click.echo(f"Summary: {output_dir/'summary.md'}")


if __name__ == "__main__":  # pragma: no cover
    main()
