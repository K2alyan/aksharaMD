"""Deterministic synthetic PDF generator for the OCR Auto Policy v1 harness.

Eight profile fixtures are produced under
``benchmarks/ocr_auto_calibration/fixtures/synthetic/``:

1. ``synth_scanned_1p.pdf`` — 1 image-only page.
2. ``synth_scanned_2p.pdf`` — 2 image-only pages.
3. ``synth_scanned_3p.pdf`` — 3 image-only pages.
4. ``synth_mixed_below_30pct.pdf`` — 20 pages, 4 image-only + 16 native.
5. ``synth_mixed_exact_30pct.pdf`` — 10 pages, 3 image-only + 7 native.
6. ``synth_mixed_above_30pct.pdf`` — 20 pages, 7 image-only + 13 native.
7. ``synth_mostly_scanned.pdf`` — 10 pages, 8 image-only + 2 native.
8. ``synth_digital_only.pdf`` — 10 pages, all native.

Each PDF ships with a sibling ``.json`` label file describing the profile
(total pages, OCR-required pages/fraction, expected backend by policy, class).

The generator is idempotent: a sibling ``.hash`` file stores the recipe hash;
files are only regenerated when the current recipe hash differs.

Invocation::

    python -m benchmarks.ocr_auto_calibration.synthetics
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_SYNTH_DIR = Path(__file__).resolve().parent / "fixtures" / "synthetic"

# Recipe schema version. Bump when the synthetic PDF construction changes so
# existing fixtures are regenerated on the next run.
_RECIPE_VERSION = "1"


@dataclass(frozen=True)
class SyntheticProfile:
    filename: str
    total_pages: int
    ocr_required_pages: int
    expected_backend_by_policy: str
    profile_class: str

    @property
    def native_pages(self) -> int:
        return self.total_pages - self.ocr_required_pages

    @property
    def ocr_required_fraction(self) -> float:
        return self.ocr_required_pages / self.total_pages if self.total_pages else 0.0


# Auto Policy v1 thresholds (informational only — used to label the fixture's
# expected policy behaviour; the harness does not enforce policy here).
_PAGE_FLOOR = 3
_FRACTION_THRESHOLD = 0.30


def _expected_backend(total: int, scanned: int) -> str:
    """Predict Auto Policy v1's preferred backend for a given (total, scanned)."""
    if scanned == 0:
        return "tesseract"
    if scanned < _PAGE_FLOOR:
        return "tesseract"
    fraction = scanned / total
    if fraction < _FRACTION_THRESHOLD:
        return "tesseract"
    return "unlimited_ocr"


def _profiles() -> list[SyntheticProfile]:
    """Return the 8 canonical synthetic profiles."""
    specs = [
        ("synth_scanned_1p.pdf", 1, 1, "scanned_below_floor"),
        ("synth_scanned_2p.pdf", 2, 2, "scanned_below_floor"),
        ("synth_scanned_3p.pdf", 3, 3, "scanned_at_floor"),
        ("synth_mixed_below_30pct.pdf", 20, 4, "mixed_below_30pct"),
        ("synth_mixed_exact_30pct.pdf", 10, 3, "mixed_exact_30pct"),
        ("synth_mixed_above_30pct.pdf", 20, 7, "mixed_above_30pct"),
        ("synth_mostly_scanned.pdf", 10, 8, "mostly_scanned"),
        ("synth_digital_only.pdf", 10, 0, "digital_only"),
    ]
    return [
        SyntheticProfile(
            filename=fname,
            total_pages=total,
            ocr_required_pages=scanned,
            expected_backend_by_policy=_expected_backend(total, scanned),
            profile_class=pclass,
        )
        for (fname, total, scanned, pclass) in specs
    ]


def _recipe_hash(profile: SyntheticProfile) -> str:
    payload = json.dumps(
        {
            "recipe_version": _RECIPE_VERSION,
            "filename": profile.filename,
            "total_pages": profile.total_pages,
            "ocr_required_pages": profile.ocr_required_pages,
            "expected_backend": profile.expected_backend_by_policy,
            "profile_class": profile.profile_class,
        },
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _build_native_page(pdf, index: int) -> None:  # type: ignore[no-untyped-def]
    """Write a page of realistic-looking native text to *pdf*."""
    page = pdf.new_page(width=595, height=842)
    lines = [
        f"Synthetic native-text page {index + 1}.",
        "This paragraph carries several sentences of extractable text so",
        "that AksharaMD's OCR-required classifier treats the page as digital.",
        "The exact wording is not important; what matters is that the page",
        "reports as text-bearing and does NOT trigger the scanned-page code",
        "path. This block is repeated with minor variation across pages to",
        "keep the fixture deterministic yet non-trivial.",
    ]
    y = 72.0
    for line in lines:
        page.insert_text((72, y), line, fontsize=11)
        y += 16.0


def _build_image_page(pdf, image_bytes: bytes) -> None:  # type: ignore[no-untyped-def]
    """Insert a rasterised image as the only content of a new page."""
    import fitz  # local import; PyMuPDF is a test-scope dependency
    page = pdf.new_page(width=595, height=842)
    rect = fitz.Rect(50, 50, 545, 792)
    page.insert_image(rect, stream=image_bytes)


def _synthetic_image_bytes() -> bytes:
    """A deterministic PNG used for every image-only page.

    In production the plan calls for rasterising a real GeoTopo page so the
    OCR content is realistic. That asset is not in the repository (fetched
    on demand via ParseBench). To keep the harness self-contained and CI-
    friendly, we substitute a small deterministic image; the OCR content
    quality is not what the harness measures on synthetic fixtures. The
    important property is that PyMuPDF's ``get_text`` reports zero characters
    for the page, so it hits the OCR-required classifier.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 800), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    # Draw a rectangle grid so the image has visible structure (helps humans
    # inspecting rendered pages) without introducing rasterised text that OCR
    # could turn into words.
    for x in range(0, 600, 60):
        draw.line([(x, 0), (x, 800)], fill=(200, 200, 200), width=1)
    for y in range(0, 800, 60):
        draw.line([(0, y), (600, y)], fill=(200, 200, 200), width=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _generate_one(profile: SyntheticProfile, out_dir: Path) -> bool:
    """Generate *profile* into *out_dir*. Returns True if the PDF was rewritten."""
    pdf_path = out_dir / profile.filename
    hash_path = pdf_path.with_suffix(".hash")
    label_path = pdf_path.with_suffix(".json")

    current_hash = _recipe_hash(profile)
    if pdf_path.exists() and hash_path.exists():
        try:
            prior = hash_path.read_text(encoding="utf-8").strip()
        except OSError:
            prior = ""
        if prior == current_hash and label_path.exists():
            return False  # up to date

    import fitz  # local import; PyMuPDF is a test-scope dependency

    image_bytes = _synthetic_image_bytes()

    pdf = fitz.open()
    try:
        # Layout: place image-only pages first, then native pages. The order
        # is deterministic and easy to reason about; the harness cares about
        # per-page OCR classification, not their order.
        for i in range(profile.ocr_required_pages):
            _build_image_page(pdf, image_bytes)
        for i in range(profile.native_pages):
            _build_native_page(pdf, i)
        pdf.save(str(pdf_path))
    finally:
        pdf.close()

    label = {
        "total_pages": profile.total_pages,
        "ocr_required_pages": profile.ocr_required_pages,
        "ocr_required_fraction": profile.ocr_required_fraction,
        "expected_backend_by_policy": profile.expected_backend_by_policy,
        "profile_class": profile.profile_class,
        "recipe_version": _RECIPE_VERSION,
    }
    with label_path.open("w", encoding="utf-8") as fh:
        json.dump(label, fh, indent=2, sort_keys=True)
    hash_path.write_text(current_hash, encoding="utf-8")
    return True


def generate_all(out_dir: Path | None = None) -> dict[str, bool]:
    """Generate every synthetic profile. Returns ``{filename: rewritten?}``."""
    directory = out_dir or _SYNTH_DIR
    directory.mkdir(parents=True, exist_ok=True)
    results: dict[str, bool] = {}
    for profile in _profiles():
        results[profile.filename] = _generate_one(profile, directory)
    return results


def main(argv: list[str] | None = None) -> int:
    _ = argv
    results = generate_all()
    for name, rewritten in results.items():
        status = "regenerated" if rewritten else "up-to-date"
        # Deliberate stdout print for the CLI entry point.
        print(f"{status}: {name}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
