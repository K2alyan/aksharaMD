"""Driver + click CLI for the parsed-vs-raw evaluation harness.

Typical invocation:

    python -m benchmarks.parsed_vs_raw.run \\
        --corpus qasper --limit 5 --questions-per-doc 3 \\
        --arms raw,markitdown,aksharamd-reference,marker \\
        --answer-model claude-haiku-4-5-20251001 \\
        --judge-model claude-haiku-4-5-20251001 \\
        --output benchmarks/results/parsed-vs-raw-qasper-pilot/

``--dry-run`` produces per-arm extractions on disk without calling any
LLM; useful for auditing the pipeline before spending API dollars.

``--fixture-mode`` uses a JSON fixture of canned LLM responses so the
whole thing runs offline (this is what the test suite exercises).

``--smoke`` overrides ``--limit`` to 1, ``--questions-per-doc`` to 1,
and ``--arms`` to a single arm (default ``aksharamd-reference``). It
hits the real Anthropic API and prints a big ``SMOKE OK`` banner on
success - the cheapest possible end-to-end verification before a real pilot.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
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

ROWS_JSONL_NAME = "rows.jsonl"

_ALL_ARMS = ("raw", "markitdown", "aksharamd-reference", "marker", "docling")
_SMOKE_DEFAULT_ARM = "aksharamd-reference"


def _load_dotenv() -> None:
    """Best-effort .env load without adding a dependency.

    Reads simple ``KEY=VALUE`` lines from a ``.env`` in the current
    working directory if present. Silently skips comments, blank lines,
    and malformed lines. Does NOT overwrite already-set environment
    variables so shell env still wins over the file.
    """
    dotenv = Path.cwd() / ".env"
    if not dotenv.is_file():
        return
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


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


def _row_key(doc_id: str, arm: str, question: str) -> tuple[str, str, str]:
    """Identity triple used to detect already-completed rows on resume."""
    return (doc_id, arm, question)


def _append_row(row: ArmResult, path: Path) -> None:
    """Persist a single ArmResult to rows.jsonl and fsync.

    Opens the file per-row so an early failure before the first row is
    written does not orphan an empty rows.jsonl that would trip the
    guard on the next non-resume invocation. Per-row open/close overhead
    is negligible next to the ~seconds each row takes for two LLM calls.

    fsync-after-write is deliberate: each row represents one paid LLM
    answer + judge call, so a mid-run kill (or crash) must not lose the
    row we just spent money on.
    """
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(row)) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _load_resumed_rows(path: Path) -> tuple[list[ArmResult], set[tuple[str, str, str]]]:
    """Read a partial rows.jsonl into ArmResult objects + a completed-key set.

    Silently skips blank lines. A malformed line raises ClickException so
    the user is not surprised by a partial-file resume - except that a
    single malformed line at the very end is treated as a mid-write kill:
    because fsync happens per row, a hard kill mid-write can leave at
    most one partial line at the tail. We discard that tail, warn on
    stderr, and continue. Any malformed line that is NOT last still
    raises (that is real corruption, not a mid-write kill).
    """
    # Read as bytes so we can compute exact truncation offsets on Windows,
    # where read_text would silently normalize CRLF -> LF and desync byte
    # counts from on-disk positions.
    raw_bytes = path.read_bytes()
    # splitlines(keepends=True) preserves \r\n or \n exactly as-is on bytes.
    byte_lines: list[bytes] = raw_bytes.splitlines(keepends=True)
    rows: list[ArmResult] = []
    keys: set[tuple[str, str, str]] = set()

    # Locate the index of the last non-blank line (if any) so we can
    # tolerate exactly one trailing malformed record.
    last_nonblank_idx = -1
    for idx in range(len(byte_lines) - 1, -1, -1):
        if byte_lines[idx].strip():
            last_nonblank_idx = idx
            break

    trailing_partial_bytes: int | None = None
    for idx, raw_line in enumerate(byte_lines):
        stripped = raw_line.strip()
        if not stripped:
            continue
        lineno = idx + 1
        try:
            text = stripped.decode("utf-8")
            payload = json.loads(text)
            row = ArmResult(**payload)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
            if idx == last_nonblank_idx:
                # Exact byte offset of the start of the partial line, so
                # truncation leaves the preceding valid rows (including
                # their line terminators) intact.
                trailing_partial_bytes = sum(len(byte_lines[j]) for j in range(idx))
                snippet = stripped[:80].decode("utf-8", errors="replace").replace("\n", " ")
                print(
                    f"Discarded trailing partial line from rows.jsonl "
                    f"(mid-write kill?): {snippet}",
                    file=sys.stderr,
                )
                break
            raise click.ClickException(
                f"Malformed row in {path} at line {lineno}: {exc}"
            ) from exc
        rows.append(row)
        keys.add(_row_key(row.doc_id, row.arm, row.question))

    if trailing_partial_bytes is not None:
        with open(path, "r+b") as handle:
            handle.truncate(trailing_partial_bytes)

    return rows, keys


def _run_arms(
    docs: Sequence[DocumentRecord],
    arms: Sequence[str],
    *,
    answer_model: str,
    judge_model: str,
    dry_run: bool,
    output_dir: Path,
    jsonl_path: Path,
    completed_keys: set[tuple[str, str, str]],
    seeded_results: Sequence[ArmResult] = (),
) -> list[ArmResult]:
    results: list[ArmResult] = list(seeded_results)
    for doc in docs:
        if not doc.pdf_bytes:
            click.echo(f"[skip] {doc.doc_id}: no PDF bytes ({doc.metadata.get('fetch_error', 'unknown')})", err=True)
            continue
        for arm_name in arms:
            for question in doc.questions:
                key = _row_key(doc.doc_id, arm_name, question.question)
                if key in completed_keys:
                    continue
                if dry_run:
                    row = _dry_run_row(doc, question, arm_name, output_dir)
                elif arm_name == "raw":
                    raw_runner = RawArm(answer_model=answer_model, judge_model=judge_model)
                    row = raw_runner.run(doc.doc_id, doc.pdf_bytes, question)
                else:
                    parser_runner = ParserArm(
                        parser=arm_name, answer_model=answer_model, judge_model=judge_model
                    )
                    row = parser_runner.run(doc.doc_id, doc.pdf_bytes, question)
                _append_row(row, jsonl_path)
                completed_keys.add(key)
                results.append(row)
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


def _print_smoke_banner(results: Sequence[ArmResult]) -> None:
    """Print a big ``SMOKE OK`` banner summarising the (single) result row."""
    if not results:
        click.echo("SMOKE FAILED: no results produced")
        return
    row = results[0]
    banner = "=" * 60
    click.echo(banner)
    click.echo("SMOKE OK")
    click.echo(banner)
    click.echo(f"  arm:              {row.arm}")
    click.echo(f"  doc_id:           {row.doc_id}")
    click.echo(f"  readiness_score:  {row.readiness_score}")
    click.echo(f"  judge_score:      {row.judge_score}")
    click.echo(f"  answer:           {row.answer[:200]}")
    if row.error:
        click.echo(f"  error:            {row.error}")
    click.echo(banner)


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
    default="raw,markitdown,aksharamd-reference,marker",
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
    default=None,
    help=(
        "Directory that receives rows.csv, summary.md, and dry-run artifacts. "
        "Required unless --smoke is passed (which picks a dated dir)."
    ),
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
@click.option(
    "--smoke",
    is_flag=True,
    help=(
        "Cheap end-to-end verification with the real Anthropic API: forces "
        "--limit 1, --questions-per-doc 1, and a single arm (default 'aksharamd-reference'; "
        "any --arms value narrows to its first element). Costs ~$0.02."
    ),
)
@click.option(
    "--resume",
    is_flag=True,
    help=(
        "Resume from an existing rows.jsonl in --output. Rows whose "
        "(doc_id, arm, question) triple is already present are skipped; "
        "new rows are appended. Without this flag, an existing rows.jsonl "
        "causes the run to error out rather than clobber prior work."
    ),
)
def main(
    corpus: str,
    limit: int,
    questions_per_doc: int,
    arms_str: str,
    answer_model: str,
    judge_model: str,
    output_dir: Path | None,
    dry_run: bool,
    fixture_mode: Path | None,
    smoke: bool,
    resume: bool,
) -> None:
    """Run the parsed-vs-raw evaluation pilot."""
    _load_dotenv()
    if smoke:
        limit = 1
        questions_per_doc = 1
        # Honour a caller-supplied --arms by picking its first element,
        # otherwise default to the aksharamd arm. Smoke is always single-arm.
        parsed = _parse_arms(arms_str)
        arms = [parsed[0]] if parsed else [_SMOKE_DEFAULT_ARM]
        if output_dir is None:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            output_dir = Path(f"parsed-vs-raw-smoke-{today}")
    else:
        arms = _parse_arms(arms_str)
    if not arms:
        raise click.BadParameter("At least one arm must be specified.")
    if output_dir is None:
        raise click.BadParameter("--output is required (unless --smoke is passed).")
    if fixture_mode is not None:
        set_client(load_fixture_client(fixture_mode))
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / ROWS_JSONL_NAME
    seeded_results: list[ArmResult] = []
    completed_keys: set[tuple[str, str, str]] = set()
    # Guard + resume-seed BEFORE any corpus load or LLM call so a misuse
    # (existing rows.jsonl without --resume) does not waste an S3 fetch
    # or a paid API call.
    if jsonl_path.exists():
        if not resume:
            raise click.ClickException(
                f"{jsonl_path} already exists; pass --resume to continue, "
                f"or choose a fresh --output dir."
            )
        seeded_results, completed_keys = _load_resumed_rows(jsonl_path)
        click.echo(
            f"Resuming from {jsonl_path}: {len(seeded_results)} rows already complete."
        )
    elif resume:
        # No partial file present: a no-op resume is not an error; behave
        # like a fresh run so scripted retries survive an empty output dir.
        click.echo(f"--resume passed but {jsonl_path} does not exist; starting fresh.")
    try:
        docs = _iter_documents(corpus, limit=limit, questions_per_doc=questions_per_doc)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    if not docs:
        raise click.ClickException("No documents loaded; corpus was empty.")
    results = _run_arms(
        docs,
        arms,
        answer_model=answer_model,
        judge_model=judge_model,
        dry_run=dry_run,
        output_dir=output_dir,
        jsonl_path=jsonl_path,
        completed_keys=completed_keys,
        seeded_results=seeded_results,
    )
    new_rows = len(results) - len(seeded_results)
    if seeded_results:
        click.echo(
            f"Resume summary: {len(seeded_results)} rows loaded, {new_rows} new rows computed."
        )
    # Sort at the final aggregation step (not during the run, which would
    # break streaming semantics) so rows.csv is byte-deterministic across
    # fresh vs. resumed runs of the same corpus + arms.
    results = sorted(results, key=lambda r: (r.doc_id, r.arm, r.question))
    write_rows_csv(results, output_dir / "rows.csv")
    summaries = summarise(results)
    write_summary_markdown(summaries, output_dir / "summary.md")
    (output_dir / "summary.json").write_text(
        json.dumps([asdict(s) for s in summaries], indent=2),
        encoding="utf-8",
    )
    click.echo(f"Wrote {len(results)} rows to {output_dir/'rows.csv'}")
    click.echo(f"Summary: {output_dir/'summary.md'}")
    if smoke:
        _print_smoke_banner(results)


if __name__ == "__main__":  # pragma: no cover
    main()
