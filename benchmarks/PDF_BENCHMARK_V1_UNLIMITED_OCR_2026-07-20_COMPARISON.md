# PDF Benchmark v1 — Unlimited-OCR pass 1 ↔ pass 2 comparison

**Total assets compared:** 45

## Classification counts

- **hash_uncomparable_pass1_missing_hash:** 39
- **exact_match:** 5
- **content_mismatch:** 1

## Cross-pass status flips (0)

_None._

## Hallucination flag changes (0)

_None._

## Per-asset classification

| Asset | Class | pass1 | pass2 | Classification | Reason |
|---|---|:-:|:-:|---|---|
| `parsebench/2colmercedes` | multicolumn | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/3colpres` | multicolumn | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/battery` | multicolumn | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/eastbaytimes` | multicolumn | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/elpais` | multicolumn | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/ikea3` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/japanese_case` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/letter3` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/myctophidae` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/simple2` | multicolumn | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/strikeUnderline` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `parsebench/text_dense__de` | multilingual | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/001-trivial/minimal-document.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/002-trivial-libre-office-writer/002-trivial-libre-office-writer.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/003-pdflatex-image/pdflatex-image.pdf` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/004-pdflatex-4-pages/pdflatex-4-pages.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/006-pdflatex-outline/pdflatex-outline.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/007-imagemagick-images/imagemagick-ASCII85Decode.pdf` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/007-imagemagick-images/imagemagick-images.pdf` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/007-imagemagick-images/imagemagick-lzw.pdf` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/008-reportlab-inline-image/inline-image.pdf` | image-only | PASS | PASS | **exact_match** | output SHA-256 identical to pass-1 inspection capture |
| `public/009-pdflatex-geotopo/GeoTopo-komprimiert.pdf` | native-text | FAIL | FAIL | **exact_match** | failure category + signature identical; signal reproduced deterministically |
| `public/009-pdflatex-geotopo/GeoTopo.pdf` | native-text | FAIL | FAIL | **exact_match** | failure category + signature identical; signal reproduced deterministically |
| `public/010-pdflatex-forms/pdflatex-forms.pdf` | malformed | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/011-google-doc-document/google-doc-document.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/012-libreoffice-form/libreoffice-form.pdf` | malformed | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/013-reportlab-overlay/reportlab-overlay.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/014-outlines/mistitled_outlines_example.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/015-arabic/habibi-oneline-cmap.pdf` | multilingual | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/015-arabic/habibi-rotated.pdf` | multilingual | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/015-arabic/habibi.pdf` | multilingual | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/016-libre-office-link/libre-office-link.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/017-unreadable-meta-data/unreadablemetadata.pdf` | native-text | FAIL | FAIL | **exact_match** | failure category + signature identical; signal reproduced deterministically |
| `public/018-base64-image/base64image.pdf` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/019-grayscale-image/grayscale-image.pdf` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/020-xmp/output_with_metadata_pymupdf.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/021-pdfa/crazyones-pdfa.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/022-pdfkit/pdfkit.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/023-cmyk-image/cmyk-image.pdf` | image-only | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/024-annotations/annotated_pdf.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/025-attachment/with-attachment.pdf` | native-text | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/026-latex-multicolumn/multicolumn.pdf` | multicolumn | PASS | PASS | **hash_uncomparable_pass1_missing_hash** | pass 1 did not record output_sha256; char_count matched (weak determinism signal) |
| `public/027-cropped-rotated-scaled/cropped-rotated-scaled.pdf` | native-text | PASS | PASS | **content_mismatch** | pass 1 reference is PARTIAL; exact match not possible. Comparing bounded prefix / hallucination-signature only. |
| `public/028-image-references-deduplication/wrong-references.pdf` | image-only | PASS | PASS | **exact_match** | output SHA-256 identical to pass-1 inspection capture |

## Notes

- Pass 1 did not record output SHA-256 for 42 of 45 assets (outputs were discarded after char-count capture). For those, the classification `hash_uncomparable_pass1_missing_hash` is used when char_count matched, and `content_mismatch` when it did not.
- The 3 manually-inspected assets DO have a pass-1 reference on disk (`benchmarks/hallucination_inspection_2026-07-20/`) and are the only assets whose exact-hash equality can be strictly verified.
- Runtime and peak-VRAM columns are diagnostic only — NOT used for the determinism classification.