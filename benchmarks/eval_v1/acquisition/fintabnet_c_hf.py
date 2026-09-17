"""FinTabNet.c acquisition from Hugging Face (bsmock/FinTabNet.c).

Resolves Git LFS OIDs (SHA-256) for the two distribution archives via the
HF tree API and writes a provenance receipt.  The actual download is
triggered only with ``--download`` (it is large: PDF_Annotations ≈ 243 MiB,
Structure ≈ 3,026 MiB).

Identity contract (Stage 1, B1a-8):
    dataset_id  = "bsmock/FinTabNet.c"
    revision    = "e5673a90b98d02c4832f9e836d72762f0e8933a0"
    license     = CDLA-Permissive-2.0
    archives    = [
        "FinTabNet.c-PDF_Annotations.tar.gz",   # ≈ 243 MiB
        "FinTabNet.c-Structure.tar.gz",          # ≈ 3,026 MiB
    ]

LFS OID as provenance:
    Git LFS stores each file's SHA-256 as the LFS ``oid`` in the tree
    manifest.  This is the SHA-256 of the archive bytes — identical to the
    digest produced by ``sha256sum`` on the downloaded file.  Recording the
    LFS OID from the pinned revision gives the full provenance chain without
    requiring a download.  A ``--download`` run verifies that the downloaded
    bytes match the OID.

Run (resolve OIDs only — fast, no download):
    python -m benchmarks.eval_v1.acquisition.fintabnet_c_hf

Run (resolve + download + verify):
    python -m benchmarks.eval_v1.acquisition.fintabnet_c_hf --download

Outputs (under --output-dir):
    acquisition.json   — provenance receipt with LFS SHA-256s
    FinTabNet.c-PDF_Annotations.tar.gz  (only with --download)
    FinTabNet.c-Structure.tar.gz        (only with --download)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DATASET_ID = "bsmock/FinTabNet.c"
REVISION = "e5673a90b98d02c4832f9e836d72762f0e8933a0"
LICENSE = "CDLA-Permissive-2.0"

HF_API_TREE = f"https://huggingface.co/api/datasets/{DATASET_ID}/tree/{REVISION}"
HF_RESOLVE_BASE = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{REVISION}"
USER_AGENT = "aksharamd-eval-v1/1.0 (+contact: ksrkklabs@gmail.com)"

ARCHIVES = [
    "FinTabNet.c-PDF_Annotations.tar.gz",
    "FinTabNet.c-Structure.tar.gz",
]

DEFAULT_OUTPUT_DIR = Path("corpus/eval_v1/fintabnet_c")


class AcquisitionError(RuntimeError):
    pass


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _request(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "huggingface.co":
        raise AcquisitionError(f"refuse non-HF HTTPS URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
        return resp.read()


def resolve_lfs_oids() -> dict[str, dict]:
    """Fetch HF tree at pinned revision and extract LFS SHA-256 + size per archive."""
    body = _request(HF_API_TREE)
    tree = json.loads(body)
    by_path: dict[str, dict] = {entry["path"]: entry for entry in tree}
    result: dict[str, dict] = {}
    for filename in ARCHIVES:
        entry = by_path.get(filename)
        if entry is None:
            raise AcquisitionError(
                f"{filename!r} not found in HF tree at revision {REVISION}"
            )
        lfs = entry.get("lfs")
        if not lfs or "oid" not in lfs:
            raise AcquisitionError(
                f"{filename!r} is not an LFS file at revision {REVISION}"
            )
        oid = lfs["oid"]
        if len(oid) != 64:
            raise AcquisitionError(
                f"{filename!r}: unexpected LFS OID length {len(oid)}: {oid!r}"
            )
        result[filename] = {
            "filename": filename,
            "url": f"{HF_RESOLVE_BASE}/{filename}",
            "lfs_sha256": oid,
            "size_bytes": lfs["size"],
            "git_oid": entry.get("oid"),
        }
    return result


def _sha256_stream(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _download(url: str, dest: Path, expected_sha256: str, *, verbose: bool = True) -> str:
    """Download url to dest, streaming. Verifies SHA-256 against expected_sha256."""
    if verbose:
        print(f"  downloading: {url}", flush=True)
        print(f"  -> {dest}", flush=True)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "huggingface.co":
        raise AcquisitionError(f"refuse non-HF HTTPS URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    h = hashlib.sha256()
    n_bytes = 0
    chunk = 1 << 20  # 1 MiB
    with urllib.request.urlopen(req, timeout=600) as resp:  # nosec B310
        with tmp.open("wb") as fh:
            while True:
                block = resp.read(chunk)
                if not block:
                    break
                fh.write(block)
                h.update(block)
                n_bytes += len(block)
                if verbose and n_bytes % (50 * chunk) == 0:
                    mb = n_bytes // (1 << 20)
                    print(f"    {mb} MiB...", flush=True)
    tmp.rename(dest)
    actual = h.hexdigest()
    if actual != expected_sha256:
        raise AcquisitionError(
            f"{dest.name}: downloaded SHA-256 {actual} != expected LFS OID "
            f"{expected_sha256}; download may be corrupt"
        )
    if verbose:
        print(f"  sha256 verified: {actual}", flush=True)
        print(f"  size: {n_bytes:,} bytes ({n_bytes // (1 << 20)} MiB)", flush=True)
    return actual


def acquire(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    download: bool = False,
    verbose: bool = True,
) -> dict:
    """Resolve LFS OIDs and optionally download archives.

    Returns the acquisition receipt dict.

    If ``download=False`` (default): records LFS SHA-256s from HF tree API
    without downloading — sufficient for corpus provenance.

    If ``download=True``: downloads both archives and verifies SHA-256s
    against the LFS OIDs.  Idempotent: existing files are verified and
    reused if SHA-256 matches.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = output_dir / "acquisition.json"

    if verbose:
        print(f"[fintabnet-c] resolving LFS OIDs from HF tree {REVISION[:8]}...",
              flush=True)
    archive_meta = resolve_lfs_oids()
    if verbose:
        for filename, meta in archive_meta.items():
            size_mib = meta["size_bytes"] // (1 << 20)
            print(
                f"  {filename}: lfs_sha256={meta['lfs_sha256'][:16]}…  "
                f"({size_mib} MiB)",
                flush=True,
            )

    archive_records: list[dict] = []
    for filename, meta in archive_meta.items():
        record = dict(meta)
        if download:
            dest = output_dir / filename
            if dest.exists():
                if verbose:
                    print(f"  verifying cached: {filename}", flush=True)
                actual = _sha256_stream(dest)
                if actual == meta["lfs_sha256"]:
                    if verbose:
                        print(f"  ok (sha256 match): {filename}", flush=True)
                    record["download_status"] = "reused_from_cache"
                    record["download_verified_utc"] = _now_utc()
                else:
                    if verbose:
                        print(
                            f"  sha256 mismatch for {filename} — re-downloading",
                            flush=True,
                        )
                    _download(
                        meta["url"], dest, meta["lfs_sha256"], verbose=verbose
                    )
                    record["download_status"] = "downloaded"
                    record["download_verified_utc"] = _now_utc()
            else:
                if verbose:
                    print(f"\n[fintabnet-c] downloading {filename}...", flush=True)
                _download(meta["url"], dest, meta["lfs_sha256"], verbose=verbose)
                record["download_status"] = "downloaded"
                record["download_verified_utc"] = _now_utc()
        else:
            record["download_status"] = "lfs_oid_resolved_not_downloaded"
        archive_records.append(record)

    receipt = {
        "schema_version": "1",
        "corpus": "fintabnet_c",
        "dataset_id": DATASET_ID,
        "revision": REVISION,
        "license": LICENSE,
        "resolved_utc": _now_utc(),
        "archives": {r["filename"]: r for r in archive_records},
        "output_dir": str(output_dir),
        "note": (
            "lfs_sha256 is the Git LFS OID, which equals SHA-256 of the archive "
            "bytes and is equivalent to the output of sha256sum on the downloaded "
            "file. It is resolved from the pinned HF tree without downloading the "
            "archive. Run with --download to fetch and verify the actual bytes."
        ),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    if verbose:
        print(f"\n[fintabnet-c] receipt -> {receipt_path}", flush=True)
    return receipt


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write archives + receipt (default: %(default)s)",
    )
    p.add_argument(
        "--download",
        action="store_true",
        help="Download the archives and verify SHA-256 against LFS OIDs (large files)",
    )
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    receipt = acquire(
        output_dir=args.output_dir,
        download=args.download,
        verbose=not args.quiet,
    )
    print("\n=== FINTABNET.C ACQUISITION COMPLETE ===", flush=True)
    for fname, rec in receipt["archives"].items():
        size_mib = rec["size_bytes"] // (1 << 20)
        print(
            f"  {fname}:  lfs_sha256={rec['lfs_sha256'][:16]}…  "
            f"({size_mib} MiB)  status={rec['download_status']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
