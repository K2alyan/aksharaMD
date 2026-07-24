"""Scientific-figure corpus loader and hydrator (Commit 3 of the
Layout Complexity v1 milestone).

Consumes :file:`science_corpus.lock.json` — the frozen list of five
public arXiv preprints introduced in Commit 1 — and provides:

* a loader that returns :class:`ScienceCorpusEntry` records with
  fully resolved cache paths (deterministic per host);
* a hydrator that fetches missing PDFs into a per-user cache root
  OUTSIDE the repo tree, and returns a structured
  :class:`HydrationResult` recording per-asset outcomes without
  raising on individual failures.

Design contract
---------------

* No import of :mod:`aksharamd.plugins.parsers.pdf` or any parser.
  The corpus loader is a metadata concern; parsing happens later in
  the capture step.
* Reference-fetch only. PDF bytes are never redistributed inside the
  repo. The cache root always lives outside ``benchmarks/`` under
  ``%LOCALAPPDATA%\\aksharamd\\science_corpus\\`` (Windows) or
  ``${XDG_CACHE_HOME:-~/.cache}/aksharamd/science_corpus/`` (POSIX);
  the fixed layout matches ``fetch_convention.cache_root_hint`` in
  the lockfile.
* Determinism. Given the same lockfile and the same cache root, the
  loader always returns entries in lockfile order with the same
  resolved paths. Hydration is idempotent — an already-present file
  with the expected sha256 is returned as ``already_present`` and
  never re-downloaded.
* No auto-mutation of the lockfile. The hydrator RECORDS the observed
  sha256 and byte size of the fetched file in the
  :class:`HydrationResult` but does NOT write back to the lockfile.
  Populating the ``expected_sha256`` / ``expected_size_bytes`` fields
  is a separate authorised step (documented in the lockfile's
  ``future_authorised_steps``).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_LOCKFILE_DEFAULT = Path(__file__).resolve().parent / "science_corpus.lock.json"

# Deliberate: the science corpus is NEVER written under the repo tree.
# The hydrator refuses to write to a cache root that resolves inside
# ``benchmarks/`` even if the caller passes such a path — that would
# violate the reference-fetch-only posture and would trip the
# repo-hygiene test that walks the working tree for stray PDFs.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BENCHMARKS_ROOT = _REPO_ROOT / "benchmarks"


def _default_cache_root() -> Path:
    """Return the deterministic per-host cache root for the science corpus.

    Matches the ``fetch_convention.cache_root_hint`` in the lockfile.
    """
    override = os.environ.get("AKSHARAMD_SCIENCE_CORPUS_CACHE")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        return Path(base) / "aksharamd" / "science_corpus"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "aksharamd" / "science_corpus"


@dataclass(frozen=True)
class ScienceCorpusEntry:
    """One paper from the science corpus.

    Field notes:

    * ``pdf_path`` is the deterministic cache location for this asset,
      even when the file is not yet present on disk. Callers can pass
      the value straight to :meth:`Path.exists` to test hydration
      status.
    * ``expected_sha256`` and ``expected_size_bytes`` mirror the
      lockfile's pinned integrity metadata. They start as ``None``
      (Phase A of the corpus lifecycle) and become populated after
      Commit 3 hydration + a follow-up lockfile edit.
    * ``expected_complexity_class`` is the a-priori class the
      layout-complexity evaluator is expected to place the doc in
      when its band boundaries are calibrated — used by the analysis
      step to compute agreement/disagreement.
    """

    document_id: str
    source: str
    arxiv_id: str
    arxiv_version: str
    pdf_url: str
    title: str
    expected_page_count_hint: int
    expected_complexity_class: str
    why_included: str
    pdf_path: Path
    expected_sha256: str | None
    expected_size_bytes: int | None

    @property
    def resolved(self) -> bool:
        return self.pdf_path.exists()


@dataclass(frozen=True)
class HydrationOutcome:
    """Per-asset result recorded by :func:`hydrate_science_corpus`.

    ``status`` is one of:

    * ``"already_present"`` — the file existed at ``pdf_path`` and (if
      an ``expected_sha256`` was pinned) matched; no download.
    * ``"downloaded"`` — the file was fetched and written atomically.
    * ``"skipped_dry_run"`` — the caller passed ``dry_run=True``; no
      network activity happened.
    * ``"integrity_mismatch"`` — the fetched bytes did not match the
      pinned sha256. The file is discarded, never installed.
    * ``"network_error"`` — the fetch failed at the network layer.
    * ``"skipped_no_url"`` — the entry has no ``pdf_url`` (shouldn't
      happen for the current lockfile but is defensive).

    ``observed_sha256`` and ``observed_size_bytes`` are populated on
    ``downloaded`` / ``already_present`` outcomes so callers can decide
    whether to promote them into the lockfile (a separate authorised
    step).
    """

    document_id: str
    status: str
    pdf_path: Path
    observed_sha256: str | None = None
    observed_size_bytes: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class HydrationResult:
    outcomes: tuple[HydrationOutcome, ...]
    cache_root: Path
    dry_run: bool

    @property
    def already_present(self) -> tuple[HydrationOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == "already_present")

    @property
    def downloaded(self) -> tuple[HydrationOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == "downloaded")

    @property
    def failed(self) -> tuple[HydrationOutcome, ...]:
        return tuple(
            o
            for o in self.outcomes
            if o.status in {"integrity_mismatch", "network_error", "skipped_no_url"}
        )


@dataclass(frozen=True)
class ScienceCorpusMetadata:
    schema_version: str
    corpus_name: str
    milestone: str
    phase: str
    redistribution_posture: dict[str, object] = field(default_factory=dict)


def load_science_corpus(
    *,
    lockfile: Path | None = None,
    cache_root: Path | None = None,
) -> tuple[ScienceCorpusMetadata, list[ScienceCorpusEntry]]:
    """Read the science-corpus lockfile and return metadata + entries.

    ``cache_root`` defaults to :func:`_default_cache_root`. Path
    resolution is stable: ``<cache_root>/<arxiv_id>_<arxiv_version>.pdf``.
    """
    lock_path = lockfile or _LOCKFILE_DEFAULT
    with lock_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    root = cache_root or _default_cache_root()

    metadata = ScienceCorpusMetadata(
        schema_version=str(payload.get("schema_version", "")),
        corpus_name=str(payload.get("corpus_name", "")),
        milestone=str(payload.get("milestone", "")),
        phase=str(payload.get("phase", "")),
        redistribution_posture=dict(payload.get("redistribution_posture", {})),
    )

    entries: list[ScienceCorpusEntry] = []
    for asset in payload.get("assets", []):
        arxiv_id = str(asset["arxiv_id"])
        arxiv_version = str(asset.get("arxiv_version", ""))
        pdf_filename = f"{arxiv_id}_{arxiv_version}.pdf"
        pdf_path = root / pdf_filename
        entries.append(
            ScienceCorpusEntry(
                document_id=str(asset["id"]),
                source=str(asset.get("source", "arxiv")),
                arxiv_id=arxiv_id,
                arxiv_version=arxiv_version,
                pdf_url=str(asset["pdf_url"]),
                title=str(asset.get("title", "")),
                expected_page_count_hint=int(asset.get("expected_page_count_hint", 0)),
                expected_complexity_class=str(
                    asset.get("expected_complexity_class", "")
                ),
                why_included=str(asset.get("why_included", "")),
                pdf_path=pdf_path,
                expected_sha256=asset.get("expected_sha256"),
                expected_size_bytes=asset.get("expected_size_bytes"),
            )
        )
    return metadata, entries


def _sha256(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _guard_cache_root(cache_root: Path) -> None:
    """Refuse to hydrate into any location under the repo tree.

    The science corpus is reference-fetch-only. Writing bytes into the
    repo tree — including gitignored subtrees — would trip
    :func:`tests.test_parsebench_page_ground_truth.test_no_pdf_files_added_to_git`
    (which walks the working tree via rglob, not git-ls-files) and
    would violate the redistribution posture pinned in the lockfile.
    """
    resolved = cache_root.resolve()
    try:
        resolved.relative_to(_REPO_ROOT.resolve())
    except ValueError:
        return
    raise ValueError(
        f"refusing to hydrate science corpus into a path under the repo "
        f"tree: {resolved}. Set AKSHARAMD_SCIENCE_CORPUS_CACHE to a "
        f"per-user cache directory outside the repo."
    )


def hydrate_science_corpus(
    *,
    entries: list[ScienceCorpusEntry],
    cache_root: Path | None = None,
    dry_run: bool = False,
    timeout_seconds: float = 30.0,
) -> HydrationResult:
    """Fetch any missing PDFs into the cache root.

    On ``dry_run=True`` the function reports what WOULD happen without
    contacting the network — useful for a smoke test in CI. Otherwise,
    for each entry:

    1. If ``pdf_path`` already exists, hash it. If ``expected_sha256``
       is pinned and matches, emit ``already_present``; if it does not
       match, emit ``integrity_mismatch``. If ``expected_sha256`` is
       ``None`` (Phase A), the observed sha is returned so a follow-up
       edit can promote it.
    2. Otherwise, fetch via HTTP into a ``.part`` temp file, hash it,
       verify against the pinned sha (when pinned), and rename into
       place atomically.

    Never raises on individual network errors — those are captured on
    :class:`HydrationOutcome`. A caller that wants to fail loud should
    check ``HydrationResult.failed``.
    """
    root = cache_root or _default_cache_root()
    _guard_cache_root(root)
    root.mkdir(parents=True, exist_ok=True)

    outcomes: list[HydrationOutcome] = []

    for entry in entries:
        if not entry.pdf_url:
            outcomes.append(
                HydrationOutcome(
                    document_id=entry.document_id,
                    status="skipped_no_url",
                    pdf_path=entry.pdf_path,
                    error="no pdf_url in lockfile entry",
                )
            )
            continue

        if entry.pdf_path.exists():
            observed_sha, observed_size = _sha256(entry.pdf_path)
            if (
                entry.expected_sha256 is not None
                and observed_sha != entry.expected_sha256
            ):
                outcomes.append(
                    HydrationOutcome(
                        document_id=entry.document_id,
                        status="integrity_mismatch",
                        pdf_path=entry.pdf_path,
                        observed_sha256=observed_sha,
                        observed_size_bytes=observed_size,
                        error=(
                            f"sha256 mismatch: expected {entry.expected_sha256}, "
                            f"observed {observed_sha}"
                        ),
                    )
                )
                continue
            outcomes.append(
                HydrationOutcome(
                    document_id=entry.document_id,
                    status="already_present",
                    pdf_path=entry.pdf_path,
                    observed_sha256=observed_sha,
                    observed_size_bytes=observed_size,
                )
            )
            continue

        if dry_run:
            outcomes.append(
                HydrationOutcome(
                    document_id=entry.document_id,
                    status="skipped_dry_run",
                    pdf_path=entry.pdf_path,
                )
            )
            continue

        outcome = _fetch_one(entry=entry, timeout_seconds=timeout_seconds)
        outcomes.append(outcome)

    return HydrationResult(
        outcomes=tuple(outcomes), cache_root=root, dry_run=dry_run
    )


def _fetch_one(
    *,
    entry: ScienceCorpusEntry,
    timeout_seconds: float,
) -> HydrationOutcome:
    part_path = entry.pdf_path.with_suffix(entry.pdf_path.suffix + ".part")
    try:
        request = urllib.request.Request(
            entry.pdf_url,
            headers={"User-Agent": "aksharamd-science-corpus-hydrator/1"},
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
            with part_path.open("wb") as fh:
                while True:
                    chunk = resp.read(1024 * 64)
                    if not chunk:
                        break
                    fh.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if part_path.exists():
            part_path.unlink(missing_ok=True)
        return HydrationOutcome(
            document_id=entry.document_id,
            status="network_error",
            pdf_path=entry.pdf_path,
            error=str(exc),
        )

    observed_sha, observed_size = _sha256(part_path)
    if (
        entry.expected_sha256 is not None
        and observed_sha != entry.expected_sha256
    ):
        part_path.unlink(missing_ok=True)
        return HydrationOutcome(
            document_id=entry.document_id,
            status="integrity_mismatch",
            pdf_path=entry.pdf_path,
            observed_sha256=observed_sha,
            observed_size_bytes=observed_size,
            error=(
                f"sha256 mismatch: expected {entry.expected_sha256}, "
                f"observed {observed_sha}"
            ),
        )
    part_path.replace(entry.pdf_path)
    return HydrationOutcome(
        document_id=entry.document_id,
        status="downloaded",
        pdf_path=entry.pdf_path,
        observed_sha256=observed_sha,
        observed_size_bytes=observed_size,
    )


__all__ = [
    "HydrationOutcome",
    "HydrationResult",
    "ScienceCorpusEntry",
    "ScienceCorpusMetadata",
    "hydrate_science_corpus",
    "load_science_corpus",
]
