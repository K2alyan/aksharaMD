"""DocLayNet acquisition against the Hugging Face v1.2 distribution.

The offline adapter (``benchmarks/eval_v1/adapters/doclaynet_v1.py``)
never opens a socket. This module is the operator-side script that
populates ``corpus/eval_v1/doclaynet/<page_hash>/`` and writes the
per-page ``manifest.json`` provenance receipt.

Identity contract (locked, B1a-3)
---------------------------------

**A DocLayNet page identity is:**

  (dataset_id, dataset_commit_sha, split, page_hash)

- The v1.2 Parquet distribution is our canonical channel; IBM COS is a
  documented fallback but is never auto-selected.
- ``page_hash`` is the sole join key across PNG / single-page PDF /
  per-page annotations / metadata. It is unique within a shard (v1.2
  is upstream-deduplicated — see §"Upstream deduplication" below).
- The bucket-level ``dataset_commit_sha`` (Hugging Face revision) is
  recorded per manifest so the exact snapshot consumed is provable.

Version policy
--------------

- One DocLayNet page contributes at most one document to any
  evaluation population.
- V1.0's `precedence` field is **not present** in v1.1 / v1.2 Parquet.
  The reformatters upstream-deduplicated: each `page_hash` appears
  exactly once in each Parquet shard. Our approved "primary
  annotations only" policy is therefore satisfied by the distribution;
  we still record ``precedence: null`` in provenance so downstream
  readers can see the field was absent, not silently defaulted.

Direct-Parquet path (no `datasets` runtime dep)
-----------------------------------------------

Uses ``huggingface_hub.HfFileSystem`` + ``pyarrow.parquet.ParquetFile``
which cooperate to do true HTTP range reads: the Parquet footer is
fetched (a few tens of KB), then only the requested columns of the
requested row group are downloaded. For prove-one, effective bandwidth
is ~65 MB (footer + one row group) rather than 519 MB (a full shard)
or 30 GB (the full corpus).

Both ``huggingface_hub`` and ``pyarrow`` are declared under the
``eval`` optional-extra; the offline adapter has no dependency on
either.
"""
from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow  # noqa: F401  (type-only)

DATASET_ID = "docling-project/DocLayNet-v1.2"
HF_REPO_PATH = f"datasets/{DATASET_ID}"
HF_API_ENDPOINT = f"https://huggingface.co/api/datasets/{DATASET_ID}"
USER_AGENT = "aksharamd-eval-v1/1.0 (+contact: ksrkklabs@gmail.com)"
MANIFEST_SCHEMA_VERSION = "1"

# DocLayNet's 11-class taxonomy (COCO category_id → name). IDs are
# 1-based per the paper/dataset card; we mirror that.
CATEGORY_ID_TO_NAME: dict[int, str] = {
    1: "Caption",
    2: "Footnote",
    3: "Formula",
    4: "List-item",
    5: "Page-footer",
    6: "Page-header",
    7: "Picture",
    8: "Section-header",
    9: "Table",
    10: "Text",
    11: "Title",
}
NAME_TO_CATEGORY_ID: dict[str, int] = {v: k for k, v in CATEGORY_ID_TO_NAME.items()}


class AcquisitionError(RuntimeError):
    """Distinct exception for identity / protocol violations during acquisition."""


# ---- HF revision resolution --------------------------------------


@dataclass(frozen=True)
class HfDatasetRef:
    dataset_id: str
    sha: str
    last_modified: str
    api_url: str


def resolve_dataset_ref(api_url: str = HF_API_ENDPOINT) -> HfDatasetRef:
    """Ask HF for the current revision SHA of the dataset.

    Returns the SHA plus last-modified timestamp so the acquisition
    manifest records exactly which snapshot was consumed. Fails closed
    on non-HTTPS URLs to keep this pinnable and auditable.
    """
    parsed = urllib.parse.urlparse(api_url)
    if parsed.scheme != "https":
        raise AcquisitionError(f"refuse to resolve non-HTTPS URL: {api_url!r}")
    if parsed.netloc != "huggingface.co":
        raise AcquisitionError(
            f"refuse to resolve URL outside huggingface.co: {api_url!r}"
        )
    req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310  (gate above)
        body = resp.read()
    info = json.loads(body)
    sha = info.get("sha")
    if not sha or len(sha) != 40:
        raise AcquisitionError(
            f"HF API returned unexpected sha for {api_url}: {sha!r}"
        )
    return HfDatasetRef(
        dataset_id=DATASET_ID,
        sha=sha,
        last_modified=info.get("lastModified", ""),
        api_url=api_url,
    )


# ---- Row model ---------------------------------------------------


@dataclass(frozen=True)
class DocLayNetAnnotation:
    """One block annotation on one page.

    ``bbox`` is ``[x, y, w, h]`` in 1025×1025 PNG-pixel space. Do NOT
    treat these as PDF-point coordinates; conversion requires
    ``coco_width`` / ``coco_height`` / ``original_width`` /
    ``original_height`` from the page-level metadata.
    """

    category_id: int
    category: str
    bbox: tuple[float, float, float, float]
    area: float


@dataclass(frozen=True)
class DocLayNetPage:
    """One page's full oracle: PNG + PDF bytes + annotations + metadata."""

    page_hash: str
    image_id: int
    original_filename: str
    page_no: int
    coco_width: int
    coco_height: int
    original_width: float
    original_height: float
    doc_category: str
    collection: str
    num_pages: int
    modalities: tuple[str, ...]
    png_bytes: bytes
    pdf_bytes: bytes | None
    annotations: tuple[DocLayNetAnnotation, ...]

    @property
    def category_names(self) -> tuple[str, ...]:
        return tuple(a.category for a in self.annotations)


def _pyarrow():
    """Lazy import so this module is only heavy when actually used."""
    import pyarrow.parquet as pq

    return pq


def _hf_fs():
    from huggingface_hub import HfFileSystem

    return HfFileSystem()


def open_parquet_shard(
    shard_key: str,
    revision: str,
):
    """Open one shard for row-group-level reads. Returns a ParquetFile."""
    pq = _pyarrow()
    fs = _hf_fs()
    path = f"{HF_REPO_PATH}/{shard_key}"
    fh = fs.open(path, mode="rb", revision=revision)
    return pq.ParquetFile(fh)


def iter_pages_in_row_group(
    pf,
    row_group: int,
    *,
    include_image: bool = False,
    include_pdf: bool = False,
):
    """Yield lightweight per-row records for one row group.

    Skips image/pdf columns by default because those are the bulk of
    the shard bytes. Enable them explicitly when a specific row has
    been selected.
    """
    columns = ["metadata", "modalities", "bboxes", "category_id", "area"]
    if include_image:
        columns.append("image")
    if include_pdf:
        columns.append("pdf")
    tbl = pf.read_row_group(row_group, columns=columns)
    n = tbl.num_rows
    md_col = tbl.column("metadata").to_pylist()
    mod_col = tbl.column("modalities").to_pylist()
    bb_col = tbl.column("bboxes").to_pylist()
    cat_col = tbl.column("category_id").to_pylist()
    area_col = tbl.column("area").to_pylist()
    img_col = tbl.column("image").to_pylist() if include_image else [None] * n
    pdf_col = tbl.column("pdf").to_pylist() if include_pdf else [None] * n
    for i in range(n):
        yield {
            "metadata": md_col[i],
            "modalities": mod_col[i],
            "bboxes": bb_col[i],
            "category_id": cat_col[i],
            "area": area_col[i],
            "image": img_col[i],
            "pdf": pdf_col[i],
        }


def build_page(row: dict[str, Any]) -> DocLayNetPage:
    md = row["metadata"]
    if row["image"] is None or "bytes" not in row["image"]:
        raise AcquisitionError(
            f"row for page_hash={md.get('page_hash')} missing image bytes"
        )
    png_bytes = row["image"]["bytes"]
    pdf_bytes = row["pdf"] if row["pdf"] else None
    bboxes = row["bboxes"]
    cats = row["category_id"]
    areas = row["area"]
    if not (len(bboxes) == len(cats) == len(areas)):
        raise AcquisitionError(
            f"annotation lengths differ for page_hash={md.get('page_hash')}: "
            f"bboxes={len(bboxes)} category_id={len(cats)} area={len(areas)}"
        )
    annotations = tuple(
        DocLayNetAnnotation(
            category_id=int(cid),
            category=CATEGORY_ID_TO_NAME.get(int(cid), f"unknown_{cid}"),
            bbox=tuple(float(x) for x in bbox),  # type: ignore[arg-type]
            area=float(a),
        )
        for cid, bbox, a in zip(cats, bboxes, areas, strict=True)
    )
    return DocLayNetPage(
        page_hash=md["page_hash"],
        image_id=int(md["image_id"]),
        original_filename=md.get("original_filename") or "",
        page_no=int(md["page_no"]),
        coco_width=int(md["coco_width"]),
        coco_height=int(md["coco_height"]),
        original_width=float(md["original_width"]),
        original_height=float(md["original_height"]),
        doc_category=md.get("doc_category") or "",
        collection=md.get("collection") or "",
        num_pages=int(md.get("num_pages") or 0),
        modalities=tuple(row["modalities"] or ()),
        png_bytes=png_bytes,
        pdf_bytes=pdf_bytes,
        annotations=annotations,
    )


# ---- Eligibility -------------------------------------------------


@dataclass(frozen=True)
class EligibilityDecision:
    ok: bool
    reason: str | None


MIN_ANNOTATIONS = 10
MAX_ANNOTATIONS = 200


def apply_page_filters(page_row_lite: dict[str, Any]) -> EligibilityDecision:
    """Structural pre-filters on a page — no image/pdf fetch required.

    Locked filter set (2026-09-14):
      - annotations non-empty
      - MIN_ANNOTATIONS <= n_annotations <= MAX_ANNOTATIONS (engineering bound,
        not a claimed population percentile)
      - >= 1 Table region
      - >= 1 additional non-Text structural region (Title / Section-header
        / Page-header / Page-footer / Caption / Footnote / Formula /
        List-item / Picture / Figure caption)

    Note on `precedence`: v1.2 upstream-deduplicated on page_hash, so
    the paper's `precedence == 0` policy is enforced by the distribution
    itself. This function does not gate on that field because it does
    not exist in the v1.2 Parquet schema.
    """
    cats: list[int] = page_row_lite["category_id"] or []
    n = len(cats)
    if n == 0:
        return EligibilityDecision(False, "no_annotations")
    if n < MIN_ANNOTATIONS:
        return EligibilityDecision(False, f"n_annotations_lt_{MIN_ANNOTATIONS}:{n}")
    if n > MAX_ANNOTATIONS:
        return EligibilityDecision(False, f"n_annotations_gt_{MAX_ANNOTATIONS}:{n}")
    table_id = NAME_TO_CATEGORY_ID["Table"]
    text_id = NAME_TO_CATEGORY_ID["Text"]
    if table_id not in cats:
        return EligibilityDecision(False, "no_table_region")
    non_text_non_table = {int(c) for c in cats if int(c) not in (text_id, table_id)}
    if not non_text_non_table:
        return EligibilityDecision(False, "no_additional_non_text_structural_region")
    return EligibilityDecision(True, None)


# ---- Manifest writer ---------------------------------------------


@dataclass(frozen=True)
class AcquiredPage:
    page_hash: str
    directory: Path
    png_path: Path
    pdf_path: Path
    annotations_path: Path
    manifest_path: Path


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def acquire_page(
    page: DocLayNetPage,
    ref: HfDatasetRef,
    shard_key: str,
    shard_sha256: str | None,
    root: Path,
    *,
    discovery_provenance: dict[str, Any] | None = None,
    selection_role: str = "corpus-adapter prove-one",
) -> AcquiredPage:
    """Write one page's assets + manifest into ``root/<page_hash>/``.

    Idempotent: if a valid manifest exists, verify hashes and reuse.
    A hash mismatch raises rather than silently overwriting.
    """
    target = root / page.page_hash
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"

    if manifest_path.exists():
        return _verify_cached(page, target, manifest_path)

    png_path = target / f"{page.page_hash}.png"
    pdf_path = target / f"{page.page_hash}.pdf"
    annotations_path = target / f"{page.page_hash}.annotations.json"

    png_path.write_bytes(page.png_bytes)
    if page.pdf_bytes:
        pdf_path.write_bytes(page.pdf_bytes)

    annotations_payload = {
        "coco_categories": [
            {"id": cid, "name": name} for cid, name in sorted(CATEGORY_ID_TO_NAME.items())
        ],
        "coordinate_space": {
            "kind": "png_pixels",
            "coco_width": page.coco_width,
            "coco_height": page.coco_height,
            "original_width": page.original_width,
            "original_height": page.original_height,
            "note": (
                "Bboxes are in COCO PNG-pixel space (coco_width × "
                "coco_height). Convert to native PDF-point space via "
                "the linear map (x → x * original_width / coco_width, "
                "y → y * original_height / coco_height, likewise w/h). "
                "See adapter's convert_bbox_to_pdf_points()."
            ),
        },
        "annotations": [
            {
                "category_id": a.category_id,
                "category": a.category,
                "bbox_png": list(a.bbox),
                "area": a.area,
            }
            for a in page.annotations
        ],
    }
    annotations_path.write_text(
        json.dumps(annotations_payload, indent=2, sort_keys=True)
    )
    ann_bytes = annotations_path.read_bytes()

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus": "doclaynet",
        "page_hash": page.page_hash,
        "acquired_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "distribution": {
            "source": "huggingface_datasets_parquet",
            "dataset_id": ref.dataset_id,
            "dataset_commit_sha": ref.sha,
            "dataset_last_modified_utc": ref.last_modified,
            "hf_api_url": ref.api_url,
            "shard_key": shard_key,
            "shard_sha256": shard_sha256,
            "split": "train",
        },
        "image_id": page.image_id,
        "original_filename": page.original_filename,
        "page_no": page.page_no,
        "doc_category": page.doc_category,
        "collection": page.collection,
        "num_pages_in_original": page.num_pages,
        "modalities": list(page.modalities),
        # v1.0 field, dropped in v1.2 Parquet reformat; recorded null so
        # downstream readers see it was absent, not silently defaulted.
        "precedence": None,
        "coordinate_space": {
            "kind": "png_pixels",
            "coco_width": page.coco_width,
            "coco_height": page.coco_height,
            "original_width": page.original_width,
            "original_height": page.original_height,
        },
        "annotations_summary": {
            "n_annotations": len(page.annotations),
            "category_distribution": _category_distribution(page.annotations),
        },
        "png": {
            "path": png_path.name,
            "sha256": _sha256(page.png_bytes),
            "size_bytes": len(page.png_bytes),
        },
        "pdf": {
            "path": pdf_path.name if page.pdf_bytes else None,
            "sha256": _sha256(page.pdf_bytes) if page.pdf_bytes else None,
            "size_bytes": len(page.pdf_bytes) if page.pdf_bytes else None,
            "present_in_distribution": page.pdf_bytes is not None,
        },
        "annotations_file": {
            "path": annotations_path.name,
            "sha256": _sha256(ann_bytes),
            "size_bytes": len(ann_bytes),
        },
        "selection": {
            "authorization": "B1a-3",
            "role": selection_role,
            "discovery": discovery_provenance or {},
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return AcquiredPage(
        page_hash=page.page_hash,
        directory=target,
        png_path=png_path,
        pdf_path=pdf_path,
        annotations_path=annotations_path,
        manifest_path=manifest_path,
    )


def _verify_cached(
    page: DocLayNetPage, target: Path, manifest_path: Path
) -> AcquiredPage:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise AcquisitionError(
            f"{manifest_path}: unsupported schema "
            f"{manifest.get('schema_version')!r}"
        )
    png_path = target / manifest["png"]["path"]
    ann_path = target / manifest["annotations_file"]["path"]
    for label, path, expected in (
        ("png", png_path, manifest["png"]["sha256"]),
        ("annotations", ann_path, manifest["annotations_file"]["sha256"]),
    ):
        actual = _sha256(path.read_bytes())
        if actual != expected:
            raise AcquisitionError(
                f"{page.page_hash}: cached {label} sha256 {actual} != "
                f"manifest {expected}; refuse to reuse — remove {target}."
            )
    pdf_meta = manifest["pdf"]
    pdf_path = target / pdf_meta["path"] if pdf_meta["path"] else target / f"{page.page_hash}.pdf"
    if pdf_meta.get("path"):
        actual = _sha256(pdf_path.read_bytes())
        if actual != pdf_meta["sha256"]:
            raise AcquisitionError(
                f"{page.page_hash}: cached pdf sha256 {actual} != "
                f"manifest {pdf_meta['sha256']}; refuse to reuse."
            )
    return AcquiredPage(
        page_hash=page.page_hash,
        directory=target,
        png_path=png_path,
        pdf_path=pdf_path,
        annotations_path=ann_path,
        manifest_path=manifest_path,
    )


def _category_distribution(annotations: tuple[DocLayNetAnnotation, ...]) -> dict[str, int]:
    d: dict[str, int] = {}
    for a in annotations:
        d[a.category] = d.get(a.category, 0) + 1
    return d


__all__ = [
    "CATEGORY_ID_TO_NAME",
    "DATASET_ID",
    "MANIFEST_SCHEMA_VERSION",
    "MAX_ANNOTATIONS",
    "MIN_ANNOTATIONS",
    "NAME_TO_CATEGORY_ID",
    "AcquiredPage",
    "AcquisitionError",
    "DocLayNetAnnotation",
    "DocLayNetPage",
    "EligibilityDecision",
    "HfDatasetRef",
    "acquire_page",
    "apply_page_filters",
    "build_page",
    "iter_pages_in_row_group",
    "open_parquet_shard",
    "resolve_dataset_ref",
]
