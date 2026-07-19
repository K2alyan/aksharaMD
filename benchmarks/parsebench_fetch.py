"""Revision-pinned ParseBench reference fetcher (Issue #53, phase B2).

Reads the frozen lockfile at `benchmarks/parsebench_assets.lock.json` and
downloads the listed assets from the LlamaIndex ParseBench dataset on
HuggingFace at the pinned dataset revision. Nothing is uploaded,
mirrored, or committed. Fetched files land under a cache root outside
git-tracked corpus paths.

## Safety contract

- Refuses to run without `AKSHARAMD_PARSEBENCH_ALLOW_NETWORK=1`.
- Refuses to use a mutable or missing `dataset_revision` (must be a
  40-character hexadecimal SHA — the value pinned in the lockfile at
  merge time).
- Refuses destinations inside the AksharaMD repo tree, inside `.git`,
  or that resolve through symlinks / path-traversal escape.
- Builds every local path from the frozen asset `id` + a fixed `.pdf`
  extension. Never reuses the remote filename directly.
- Downloads atomically via temp file + rename; interrupted downloads
  never poison the cache.
- Never populates `sha256`, `size_bytes`, or `mirror_url` back into the
  lockfile. Checksum capture is a separate authorised step.
- On success, records identity-only verification. It is a policy
  violation to represent an unchecked file as "verified for
  calibration".

## Error codes

| Code | Meaning |
|---:|---|
| 0  | All requested assets present in cache, identity verified. |
| 10 | `AKSHARAMD_PARSEBENCH_ALLOW_NETWORK` not set (or not `1`). |
| 11 | Lockfile missing or invalid JSON. |
| 12 | Lockfile schema invariants violated (e.g. non-reference-fetch redistribution, populated mirror URL). |
| 13 | Asset id not found in lockfile (`--asset ID` selector). |
| 14 | Unresolved identity (an asset entry lacks a `filename` or `hf_repo_path`). |
| 15 | Missing / mutable dataset revision (not 40-char hex). |
| 20 | Network unreachable / DNS / TCP / TLS failure. |
| 21 | Provider authentication failure (401 / 403). |
| 22 | Asset not present at the pinned revision (404 or manifest miss). |
| 23 | Checksum mismatch. Reserved: unreachable in phase B2 because no checksums are populated yet. |
| 24 | HTTP or provider failure other than the categories above. |
| 25 | Unsafe destination (inside repo tree, escapes cache root, or symlink loop). |
| 26 | Local filesystem failure (permission denied, disk full, partial write). |

Exit code is the maximum error code seen across the batch when running
in `--all` mode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

_HF_REPO = "llamaindex/ParseBench"
_HF_BASE = "https://huggingface.co/datasets"
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_EXT = ".pdf"


class FetchError(Exception):
    """Raised for a specific numeric error code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class FetchOutcome:
    asset_id: str
    filename: str | None
    hf_repo_path: str | None
    destination: str | None
    status: str  # "fetched" | "cached" | "error" | "skipped-unresolved"
    error_code: int
    error: str
    bytes_downloaded: int | None = None
    validation: str = "identity-only"
    checksum_status: str = "unavailable"
    # Two-level calibration approval — do NOT collapse into one Boolean.
    # A document-approved asset can still lack the page-level annotations
    # required for honest per-page metrics.
    approved_for_document_calibration: bool = False
    approved_for_page_calibration: bool = False
    calibration_reason: str = ""


@dataclass
class FetchReport:
    lockfile: str
    dataset_repo: str
    dataset_revision: str
    started_at: str
    finished_at: str
    outcomes: list[FetchOutcome] = field(default_factory=list)
    exit_code: int = 0


# ── lockfile handling ────────────────────────────────────────────────────


def load_lockfile(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError as exc:
        raise FetchError(11, f"lockfile not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FetchError(11, f"lockfile is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or "assets" not in doc:
        raise FetchError(11, "lockfile missing top-level 'assets'")
    return doc


def _check_lockfile_invariants(doc: dict) -> None:
    """Reject a lockfile whose contents no longer match the phase-B1 policy.

    Called AFTER `load_lockfile` and BEFORE any network activity so an
    accidental mutation (e.g. someone edited `redistribution` to `direct`
    without following the review process) fails fast with a clear code.
    """
    for entry in doc["assets"]:
        aid = entry.get("id")
        if entry.get("redistribution") != "reference-fetch-only":
            raise FetchError(
                12,
                f"asset {aid!r}: redistribution must be 'reference-fetch-only', "
                f"got {entry.get('redistribution')!r}. "
                "Reclassification requires review + rights_review_queue update.",
            )
        for field_name in ("mirror_url", "binary_url"):
            if entry.get(field_name):
                raise FetchError(
                    12,
                    f"asset {aid!r}: {field_name} must be null in phase B2 — "
                    "the fetcher is not permitted to consume a mirror.",
                )


def _resolve_revision(doc: dict) -> str:
    override = os.environ.get("AKSHARAMD_PARSEBENCH_REVISION")
    if override:
        rev = override
    else:
        rev = ((doc.get("dataset_source") or {}).get("dataset_revision") or "").strip()
    if not rev:
        raise FetchError(15, "dataset_revision is null in the lockfile; no revision to pin against.")
    if not _REVISION_RE.match(rev):
        raise FetchError(
            15,
            f"dataset_revision={rev!r} is not a 40-character hexadecimal SHA. "
            "The fetcher refuses to consume mutable refs (e.g. 'main').",
        )
    return rev


def _resolve_asset(entry: dict) -> tuple[str, str, str]:
    aid = entry.get("id") or "<unknown>"
    filename = entry.get("filename")
    hf_path = entry.get("hf_repo_path")
    if not filename or not hf_path:
        raise FetchError(
            14,
            f"asset {aid!r}: identity unresolved (filename or hf_repo_path missing)",
        )
    return aid, filename, hf_path


# ── safety helpers ───────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Best-effort locate the AksharaMD repo root so we can refuse
    destinations inside it.
    """
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    return here.parent


def _validate_destination(cache_root: Path, dest: Path) -> None:
    """Refuse destinations that leak inside the repo tree, escape the
    cache root, or resolve through symlinks unexpectedly.
    """
    try:
        real_root = cache_root.resolve()
        real_dest = dest.resolve()
    except OSError as exc:
        raise FetchError(25, f"cannot resolve destination path: {exc}") from exc

    if real_root == real_dest:
        raise FetchError(25, "destination equals cache root")

    try:
        real_dest.relative_to(real_root)
    except ValueError as exc:
        raise FetchError(25, f"destination {real_dest} escapes cache root {real_root}") from exc

    repo = _repo_root().resolve()
    try:
        real_dest.relative_to(repo)
        raise FetchError(25, f"destination {real_dest} is inside the repo tree at {repo}")
    except ValueError:
        pass  # good: not inside the repo tree


def _build_destination(cache_root: Path, revision: str, asset_id: str) -> Path:
    """Build a deterministic path from the frozen asset id + fixed extension.

    Never uses a filename supplied by the remote source — this prevents a
    malicious lockfile mutation from writing outside the intended layout.
    """
    if not re.match(r"^[A-Za-z0-9_.\-]+$", asset_id):
        raise FetchError(25, f"asset id {asset_id!r} contains characters we refuse to place on disk")
    return cache_root / revision / (asset_id + _ALLOWED_EXT)


# ── HTTP fetch ───────────────────────────────────────────────────────────


HttpFetch = Callable[[str], tuple[int, bytes]]
"""Signature: (url) -> (status_code, body_bytes). Injectable for tests."""


def _default_http_fetch(url: str) -> tuple[int, bytes]:
    """urllib-based fetch. Only invoked when
    `AKSHARAMD_PARSEBENCH_ALLOW_NETWORK=1` is set — enforced by `main`.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "aksharamd/parsebench-fetch/1.0"},
    )
    try:
        # URL is constructed from HF_BASE + a whitelisted repo + a 40-char hex
        # revision + a lockfile-controlled path; no user-supplied scheme.
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            body = resp.read()
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except urllib.error.URLError as exc:
        # Network unreachable / DNS / TLS
        raise FetchError(20, f"network unreachable: {exc.reason}") from exc
    except TimeoutError as exc:  # pragma: no cover - hard to hit in unit tests
        raise FetchError(20, f"network timeout: {exc}") from exc


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _document_label_valid(entry: dict) -> bool:
    """Document-level metadata gate. An asset needs both `expected_label`
    (positive/negative classification) AND `defect_kind` (block-level /
    span-level / mixed) to be scorable at the document level.
    """
    return bool(entry.get("expected_label")) and bool(entry.get("defect_kind"))


def _page_ground_truth_valid(entry: dict) -> bool:
    """Page-level metadata gate. `page_level_ground_truth` must be
    non-null AND non-empty for per-page metrics to be honest.
    """
    gt = entry.get("page_level_ground_truth")
    return gt is not None and bool(gt)


def _compute_calibration_gates(
    entry: dict, checksum_status: str
) -> tuple[bool, bool, str]:
    """Return (document_approval, page_approval, reason).

    reason is a machine-readable short string explaining the FIRST failed
    gate encountered, or the empty string when all gates pass. Downstream
    consumers key on this string, not on the free-form error field.
    """
    if checksum_status != "verified":
        return False, False, f"checksum_status_{checksum_status}"
    if not _document_label_valid(entry):
        # Which piece of document metadata is missing?
        if not entry.get("expected_label"):
            return False, False, "expected_label_missing"
        return False, False, "defect_kind_missing"
    if not _page_ground_truth_valid(entry):
        return True, False, "page_level_ground_truth_missing"
    return True, True, ""


def _verify_local_bytes(entry: dict, dest: Path) -> tuple[str, str, str]:
    """Verify a cached file against the lockfile's promoted sha256/size.

    Returns (checksum_status, validation, error_message).

    - If the lockfile has no sha256 (still Phase B2 style), status is
      "unavailable" and validation is "identity-only".
    - If the lockfile has sha256 and the file matches, status is
      "verified" and validation is "checksum-verified".
    - If mismatch, error_message is populated and the caller returns
      code 23 (checksum mismatch).
    """
    expected_sha = entry.get("sha256")
    expected_size = entry.get("size_bytes")
    if not expected_sha:
        return "unavailable", "identity-only", ""
    actual_size = dest.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        return "mismatch", "checksum-verified", (
            f"size mismatch: expected {expected_size}, got {actual_size}"
        )
    actual_sha = _sha256_of_file(dest)
    if actual_sha != expected_sha:
        return "mismatch", "checksum-verified", (
            f"sha256 mismatch: expected {expected_sha}, got {actual_sha}"
        )
    return "verified", "checksum-verified", ""


def _fetch_one(
    entry: dict,
    cache_root: Path,
    revision: str,
    http_fetch: HttpFetch,
) -> FetchOutcome:
    try:
        aid, filename, hf_path = _resolve_asset(entry)
    except FetchError as exc:
        return FetchOutcome(
            asset_id=entry.get("id") or "<unknown>",
            filename=entry.get("filename"),
            hf_repo_path=entry.get("hf_repo_path"),
            destination=None,
            status="skipped-unresolved",
            error_code=exc.code,
            error=exc.message,
        )

    dest = _build_destination(cache_root, revision, aid)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _validate_destination(cache_root, dest)

    if dest.exists() and dest.stat().st_size > 0:
        checksum_status, validation, mismatch_msg = _verify_local_bytes(entry, dest)
        if checksum_status == "mismatch":
            return FetchOutcome(
                asset_id=aid,
                filename=filename,
                hf_repo_path=hf_path,
                destination=str(dest),
                status="error",
                error_code=23,
                error=mismatch_msg,
                validation=validation,
                checksum_status=checksum_status,
                approved_for_document_calibration=False,
                approved_for_page_calibration=False,
                calibration_reason="checksum_mismatch",
            )
        doc_ok, page_ok, reason = _compute_calibration_gates(entry, checksum_status)
        return FetchOutcome(
            asset_id=aid,
            filename=filename,
            hf_repo_path=hf_path,
            destination=str(dest),
            status="cached",
            error_code=0,
            error="",
            bytes_downloaded=None,
            validation=validation,
            checksum_status=checksum_status,
            approved_for_document_calibration=doc_ok,
            approved_for_page_calibration=page_ok,
            calibration_reason=reason,
        )

    url = f"{_HF_BASE}/{_HF_REPO}/resolve/{revision}/{hf_path}"
    try:
        status, body = http_fetch(url)
    except FetchError:
        raise
    if status == 401 or status == 403:
        return FetchOutcome(
            asset_id=aid, filename=filename, hf_repo_path=hf_path,
            destination=None, status="error",
            error_code=21, error=f"HTTP {status} from provider",
        )
    if status == 404:
        return FetchOutcome(
            asset_id=aid, filename=filename, hf_repo_path=hf_path,
            destination=None, status="error",
            error_code=22,
            error=f"asset not present at revision {revision}",
        )
    if status >= 400:
        return FetchOutcome(
            asset_id=aid, filename=filename, hf_repo_path=hf_path,
            destination=None, status="error",
            error_code=24, error=f"HTTP {status} from provider",
        )
    if not body:
        return FetchOutcome(
            asset_id=aid, filename=filename, hf_repo_path=hf_path,
            destination=None, status="error",
            error_code=24, error="empty response body",
        )

    # Atomic write via temp file + rename
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=f".{aid}.", suffix=".part", dir=str(dest.parent))
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(body)
        os.replace(tmp_name, dest)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        return FetchOutcome(
            asset_id=aid, filename=filename, hf_repo_path=hf_path,
            destination=None, status="error",
            error_code=26, error=f"local filesystem failure: {exc}",
        )

    checksum_status, validation, mismatch_msg = _verify_local_bytes(entry, dest)
    if checksum_status == "mismatch":
        return FetchOutcome(
            asset_id=aid, filename=filename, hf_repo_path=hf_path,
            destination=str(dest), status="error",
            error_code=23, error=mismatch_msg,
            bytes_downloaded=len(body),
            validation=validation,
            checksum_status=checksum_status,
            approved_for_document_calibration=False,
            approved_for_page_calibration=False,
            calibration_reason="checksum_mismatch",
        )
    doc_ok, page_ok, reason = _compute_calibration_gates(entry, checksum_status)
    return FetchOutcome(
        asset_id=aid, filename=filename, hf_repo_path=hf_path,
        destination=str(dest),
        status="fetched",
        error_code=0, error="",
        bytes_downloaded=len(body),
        validation=validation,
        checksum_status=checksum_status,
        approved_for_document_calibration=doc_ok,
        approved_for_page_calibration=page_ok,
        calibration_reason=reason,
    )


# ── driver ───────────────────────────────────────────────────────────────


def fetch(
    lockfile_path: Path,
    cache_root: Path,
    asset_selector: str | None = None,
    http_fetch: HttpFetch | None = None,
    now_fn: Callable[[], str] | None = None,
) -> FetchReport:
    """Library entry point (also used by tests).

    Callers are responsible for enforcing the
    `AKSHARAMD_PARSEBENCH_ALLOW_NETWORK` gate — the library-level API
    only enforces every OTHER contract clause so tests can drive it with
    a mock HTTP fetch without setting the env var.
    """
    if http_fetch is None:
        http_fetch = _default_http_fetch
    if now_fn is None:
        def now_fn() -> str:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    doc = load_lockfile(lockfile_path)
    _check_lockfile_invariants(doc)
    revision = _resolve_revision(doc)

    outcomes: list[FetchOutcome] = []
    started = now_fn()

    if asset_selector is not None:
        entries = [e for e in doc["assets"] if e.get("id") == asset_selector]
        if not entries:
            raise FetchError(13, f"asset id {asset_selector!r} not found in lockfile")
    else:
        entries = list(doc["assets"])

    for entry in entries:
        outcomes.append(_fetch_one(entry, cache_root, revision, http_fetch))

    finished = now_fn()
    exit_code = max((o.error_code for o in outcomes), default=0)
    return FetchReport(
        lockfile=str(lockfile_path),
        dataset_repo=_HF_REPO,
        dataset_revision=revision,
        started_at=started,
        finished_at=finished,
        outcomes=outcomes,
        exit_code=exit_code,
    )


def _write_report(report: FetchReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "lockfile": report.lockfile,
        "dataset_repo": report.dataset_repo,
        "dataset_revision": report.dataset_revision,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "exit_code": report.exit_code,
        "outcomes": [asdict(o) for o in report.outcomes],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=False)


def _default_cache_root() -> Path:
    override = os.environ.get("AKSHARAMD_PARSEBENCH_CACHE")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        return Path(base) / "aksharamd" / "parsebench"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "aksharamd" / "parsebench"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lockfile", type=Path, default=Path(__file__).parent / "parsebench_assets.lock.json")
    ap.add_argument("--cache-root", type=Path, default=None,
                    help="Where to store fetched files (must be outside the repo tree). Defaults to a user cache path.")
    ap.add_argument("--asset", type=str, default=None,
                    help="Fetch only this asset id. Default: all resolved assets in the lockfile.")
    ap.add_argument("--report", type=Path, default=None,
                    help="Write a machine-readable JSON report to this path.")
    args = ap.parse_args(argv)

    if os.environ.get("AKSHARAMD_PARSEBENCH_ALLOW_NETWORK") != "1":
        print(
            "REFUSED: AKSHARAMD_PARSEBENCH_ALLOW_NETWORK is not set to 1. "
            "This fetcher makes network requests to LlamaIndex ParseBench; "
            "you must acknowledge that before it will run.",
            file=sys.stderr,
        )
        return 10

    cache_root = args.cache_root or _default_cache_root()

    try:
        report = fetch(args.lockfile, cache_root, asset_selector=args.asset)
    except FetchError as exc:
        print(f"REFUSED (code {exc.code}): {exc.message}", file=sys.stderr)
        return exc.code

    if args.report is not None:
        _write_report(report, args.report)

    # Human-readable stderr summary
    for outcome in report.outcomes:
        status_word = outcome.status.upper()
        print(
            f"[{outcome.asset_id:<20s}] {status_word} code={outcome.error_code} "
            f"dest={outcome.destination or '-'} error={outcome.error!r}",
            file=sys.stderr,
        )
    if report.exit_code == 0:
        print(
            "NOTE: checksums are unavailable and this fetch is IDENTITY-ONLY. "
            "The cached files are NOT approved for calibration until an authorised "
            "checksum-capture step has recorded and reviewed sha256 values.",
            file=sys.stderr,
        )

    # Cleanup pass — if the caller runs on a shared workstation, offer to
    # remove the whole cache dir; we do NOT do this automatically.
    _ = shutil  # kept imported for the atomic-move implementation

    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
