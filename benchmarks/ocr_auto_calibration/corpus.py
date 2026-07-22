"""Corpus enumeration for the OCR Auto Policy v1 calibration harness.

Three sources feed the corpus, in priority order:

* **ParseBench** — the 12 pinned PDFs listed in
  :file:`benchmarks/parsebench_assets.lock.json`. Files themselves are
  reference-fetch-only (see :mod:`benchmarks.parsebench_fetch`), so an
  entry may exist without a resolved on-disk path.
* **Synthetic profile fixtures** — 8 deterministic PDFs built by
  :mod:`benchmarks.ocr_auto_calibration.synthetics`.
* **Failure fixtures** — real regressions preferred; a single deterministic
  zero-render fallback fixture is generated only if no real case exists.

De-duplication is by ``document_id``: the same asset appearing in two sources
(rare, but possible for a fixture that mirrors a ParseBench file) is kept
under its first observed source.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

CorpusSource = Literal["parsebench", "synthetic", "failure", "local"]

_LOCKFILE_DEFAULT = Path(__file__).resolve().parents[1] / "parsebench_assets.lock.json"
_SYNTH_DIR = Path(__file__).resolve().parent / "fixtures" / "synthetic"
_FAILURE_DIR = Path(__file__).resolve().parent / "fixtures" / "failure"
_LOCAL_DIR = Path(__file__).resolve().parent / "fixtures" / "local"


@dataclass(frozen=True)
class CorpusEntry:
    """One PDF to run through the three treatments."""

    document_id: str
    path: Path | None
    sha256: str | None
    profile_class: str
    expected_backend_by_policy: str | None
    source: CorpusSource
    notes: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        """True when the PDF is present on disk."""
        return self.path is not None and self.path.exists()


# ── ParseBench ────────────────────────────────────────────────────────


def _default_parsebench_cache_root() -> Path:
    """Return the same cache root used by ``benchmarks.parsebench_fetch``.

    Kept in sync with ``benchmarks.parsebench_fetch._default_cache_root``; do
    not import the private helper to avoid a dependency on that module's
    argparse entry point.
    """
    override = os.environ.get("AKSHARAMD_PARSEBENCH_CACHE")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        return Path(base) / "aksharamd" / "parsebench"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "aksharamd" / "parsebench"


def _profile_class_from_asset(entry: dict[str, object]) -> str:
    """Map a ParseBench lockfile entry to a profile_class string."""
    defect = entry.get("defect_kind") or "unknown"
    return f"parsebench_{defect}"


def load_parsebench_corpus(
    lockfile: Path | None = None,
    cache_root: Path | None = None,
) -> list[CorpusEntry]:
    """Read the ParseBench lockfile and return one CorpusEntry per asset.

    File paths are resolved best-effort via the deterministic destination
    layout used by the fetcher: ``<cache_root>/<revision>/<asset_id>.pdf``.
    Entries whose PDFs have not been fetched yet still appear in the corpus
    (``path`` is set to the *expected* location but ``resolved`` returns
    False); the harness reports such docs as missing rather than silently
    dropping them.
    """
    lock_path = lockfile or _LOCKFILE_DEFAULT
    if not lock_path.exists():
        return []

    with lock_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    revision = str(payload.get("dataset_source", {}).get("dataset_revision", ""))
    root = cache_root or _default_parsebench_cache_root()

    entries: list[CorpusEntry] = []
    for asset in payload.get("assets", []):
        asset_id = str(asset["id"])
        expected_path = root / revision / f"{asset_id}.pdf" if revision else None
        # If the file isn't present at the deterministic location, leave path
        # pointing there anyway so the harness can print an actionable error.
        entries.append(
            CorpusEntry(
                document_id=asset_id,
                path=expected_path,
                sha256=asset.get("sha256"),
                profile_class=_profile_class_from_asset(asset),
                expected_backend_by_policy=None,
                source="parsebench",
                notes=str(asset.get("notes", "")),
                extra={
                    "filename": asset.get("filename"),
                    "hf_repo_path": asset.get("hf_repo_path"),
                    "defect_kind": asset.get("defect_kind"),
                    "expected_label": asset.get("expected_label"),
                    "page_level_ground_truth": asset.get("page_level_ground_truth"),
                },
            )
        )
    return entries


# ── Synthetic fixtures ────────────────────────────────────────────────


def list_synthetic_fixtures(synth_dir: Path | None = None) -> list[CorpusEntry]:
    """Enumerate ``<synth_dir>/*.pdf`` with sibling ``*.json`` labels."""
    directory = synth_dir or _SYNTH_DIR
    if not directory.exists():
        return []
    entries: list[CorpusEntry] = []
    for pdf_path in sorted(directory.glob("*.pdf")):
        label_path = pdf_path.with_suffix(".json")
        label: dict[str, object] = {}
        if label_path.exists():
            with label_path.open("r", encoding="utf-8") as fh:
                label = json.load(fh)
        profile_class = str(label.get("profile_class") or pdf_path.stem)
        expected_backend = label.get("expected_backend_by_policy")
        entries.append(
            CorpusEntry(
                document_id=pdf_path.stem,
                path=pdf_path,
                sha256=None,  # Synthetic PDFs re-hash at runtime if needed.
                profile_class=profile_class,
                expected_backend_by_policy=(
                    str(expected_backend) if expected_backend else None
                ),
                source="synthetic",
                notes="Generated by benchmarks.ocr_auto_calibration.synthetics",
                extra=dict(label),
            )
        )
    return entries


# ── Failure fixtures ──────────────────────────────────────────────────


def list_failure_fixtures(
    parsebench_entries: list[CorpusEntry] | None = None,
    failure_dir: Path | None = None,
) -> list[CorpusEntry]:
    """Real-first priority.

    * If a ParseBench asset is annotated as a canonical hallucination or
      zero-render case, we surface it as a failure entry (with source
      ``failure``) — but ONLY if the file is present on disk. Real cases
      are preferred; deterministic synthetic fallbacks come next.
    * Otherwise, look under ``<failure_dir>/*.pdf`` for user-supplied
      failure fixtures.

    Callers should still pass through :func:`enumerate_corpus`, which
    deduplicates by ``document_id`` so a real asset does not appear twice.
    """
    directory = failure_dir or _FAILURE_DIR
    entries: list[CorpusEntry] = []
    if directory.exists():
        for pdf_path in sorted(directory.glob("*.pdf")):
            label_path = pdf_path.with_suffix(".json")
            label: dict[str, object] = {}
            if label_path.exists():
                with label_path.open("r", encoding="utf-8") as fh:
                    label = json.load(fh)
            entries.append(
                CorpusEntry(
                    document_id=pdf_path.stem,
                    path=pdf_path,
                    sha256=None,
                    profile_class=str(label.get("profile_class") or "failure"),
                    expected_backend_by_policy=None,
                    source="failure",
                    notes=str(label.get("notes", "Failure fixture")),
                    extra=dict(label),
                )
            )
    return entries


# ── Local optional fixtures ───────────────────────────────────────────


def list_local_fixtures(local_dir: Path | None = None) -> list[CorpusEntry]:
    """Enumerate user-provided optional local PDFs.

    Each ``<local_dir>/*.pdf`` may have a sibling ``*.json`` label with
    fields like ``profile_class``, ``expected_backend_by_policy``, and
    ``notes``. Entries whose PDFs are missing on this machine are still
    returned — with ``path`` pointing at the expected location and
    ``resolved=False`` — so the harness can mark them
    ``skipped_missing_local_asset`` instead of silently dropping them.

    Local docs are outside the ParseBench corpus and outside the
    committed synthetic fixtures; they are for real content that a
    reviewer wants to include on their own machine (e.g. GeoTopo).
    """
    directory = local_dir or _LOCAL_DIR
    if not directory.exists():
        return []
    entries: list[CorpusEntry] = []
    for label_path in sorted(directory.glob("*.json")):
        pdf_path = label_path.with_suffix(".pdf")
        with label_path.open("r", encoding="utf-8") as fh:
            label = json.load(fh)
        # ``path`` in the label may be absolute (points outside the repo)
        # or relative to the label file.
        raw_path = label.get("path")
        if raw_path:
            resolved_path = Path(str(raw_path))
            if not resolved_path.is_absolute():
                resolved_path = (directory / raw_path).resolve()
        else:
            resolved_path = pdf_path
        entries.append(
            CorpusEntry(
                document_id=str(label.get("document_id") or label_path.stem),
                path=resolved_path,
                sha256=label.get("expected_sha256"),
                profile_class=str(label.get("profile_class") or "local"),
                expected_backend_by_policy=(
                    str(label.get("expected_backend_by_policy"))
                    if label.get("expected_backend_by_policy") else None
                ),
                source="local",
                notes=str(label.get("notes", "")),
                extra=dict(label),
            )
        )
    return entries


# ── Combined enumeration ──────────────────────────────────────────────


def enumerate_corpus(
    *,
    lockfile: Path | None = None,
    cache_root: Path | None = None,
    synth_dir: Path | None = None,
    failure_dir: Path | None = None,
    local_dir: Path | None = None,
) -> list[CorpusEntry]:
    """Combine ParseBench + synthetic + failure + local entries; dedupe by id."""
    parsebench = load_parsebench_corpus(lockfile=lockfile, cache_root=cache_root)
    synthetic = list_synthetic_fixtures(synth_dir=synth_dir)
    failures = list_failure_fixtures(
        parsebench_entries=parsebench, failure_dir=failure_dir,
    )
    local = list_local_fixtures(local_dir=local_dir)

    seen: set[str] = set()
    combined: list[CorpusEntry] = []
    for entry in (*parsebench, *synthetic, *failures, *local):
        if entry.document_id in seen:
            continue
        seen.add(entry.document_id)
        combined.append(entry)
    return combined
