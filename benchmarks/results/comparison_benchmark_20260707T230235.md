# AksharaMD Multi-Tool Comparison Benchmark

**Generated:** 2026-07-07 23:09 UTC  
**Total files:** 134  
**Elapsed:** 443.7s  
**Tools:** aksharamd, markitdown, docling  

## Overall Summary

| Metric | Aksharamd | Markitdown | Docling |
| --- | --- | --- | --- |
| Files attempted | 134 | 134 | 134 |
| Succeeded | 133 | 133 | 72 |
| Success rate | 99% | 99% | 54% |
| Avg tokens (success) | 557 | 1,846 | 630 |
| Avg chars (success) | 2,232 | 7,386 | 2,525 |
| Avg elapsed | 0.39s | 0.17s | 3.55s |

## Token Efficiency vs AksharaMD

| Format | Aksharamd tokens | Markitdown tokens | Docling tokens | Ratio (AksharaMD÷other) |
| --- | --- | --- | --- | --- |
| csv | 88 | 78 | - | 0.9× |
| docx | 47 | 84 | 88 | 1.8×, 1.8× |
| html | 37 | 44 | 48 | 1.2×, 1.3× |
| json | 191 | 121 | - | 0.6× |
| md | 67 | 108 | - | 1.6× |
| pdf | 1,970 | 7,135 | 1,327 | 3.6×, 0.7× |
| pptx | 52 | 69 | 57 | 1.3×, 1.1× |
| txt | 72 | 140 | - | 1.9× |
| xlsx | 71 | 73 | 99 | 1.0×, 1.4× |
| xml | 53 | 76 | - | 1.4× |
| zip | 232 | 209 | - | 0.9× |

## Per-File Results

| ID | Format | Aksharamd tokens | Markitdown tokens | Docling tokens | Aksharamd ok? | Markitdown ok? | Docling ok? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| pdf-001 | pdf | 148 | 149 | 147 | ✓ | ✓ | ✓ |
| pdf-002 | pdf | 147 | 148 | 147 | ✓ | ✓ | ✓ |
| pdf-003 | pdf | 155 | 184 | 156 | ✓ | ✓ | ✓ |
| pdf-004 | pdf | 696 | 3,621 | 3,616 | ✓ | ✓ | ✓ |
| pdf-005 | pdf | — (error) | — (exception) | — (exception) | error | exception | exception |
| pdf-006 | pdf | 1,468 | 1,937 | 1,940 | ✓ | ✓ | ✓ |
| pdf-007 | pdf | 0 | 0 | 0 | ✓ | ✓ | ✓ |
| pdf-008 | pdf | 0 | 13 | 0 | ✓ | ✓ | ✓ |
| pdf-009 | pdf | 0 | 0 | 0 | ✓ | ✓ | ✓ |
| pdf-010 | pdf | 6 | 1 | 3 | ✓ | ✓ | ✓ |
| pdf-011 | pdf | 0 | 6 | 4 | ✓ | ✓ | ✓ |
| pdf-012 | pdf | 304 | 382 | 371 | ✓ | ✓ | ✓ |
| pdf-013 | pdf | 30 | 30 | 36 | ✓ | ✓ | ✓ |
| pdf-014 | pdf | 16 | 16 | 0 | ✓ | ✓ | ✓ |
| pdf-015 | pdf | 1,468 | 1,937 | 1,940 | ✓ | ✓ | ✓ |
| pdf-016 | pdf | 3 | 5 | 5 | ✓ | ✓ | ✓ |
| pdf-017 | pdf | 3 | 26 | 5 | ✓ | ✓ | ✓ |
| pdf-018 | pdf | 15 | 9 | 15 | ✓ | ✓ | ✓ |
| pdf-019 | pdf | 346 | 630 | — (exception) | ✓ | ✓ | exception |
| pdf-020 | pdf | 53 | 17 | 33 | ✓ | ✓ | ✓ |
| pdf-021 | pdf | 6 | 0 | 3 | ✓ | ✓ | ✓ |
| pdf-022 | pdf | 0 | 3 | 0 | ✓ | ✓ | ✓ |
| pdf-023 | pdf | 225 | 227 | 229 | ✓ | ✓ | ✓ |
| pdf-024 | pdf | 10 | 11 | 3 | ✓ | ✓ | ✓ |
| pdf-025 | pdf | 1,849 | 3,300 | 1,873 | ✓ | ✓ | ✓ |
| pdf-026 | pdf | 0 | 0 | 0 | ✓ | ✓ | ✓ |
| pdf-027 | pdf | 29,351 | 109,171 | 15,862 | ✓ | ✓ | ✓ |
| pdf-028 | pdf | 28,465 | 113,351 | 15,861 | ✓ | ✓ | ✓ |
| pdf-029 | pdf | 3 | 5 | 5 | ✓ | ✓ | ✓ |
| pdf-030 | pdf | 6 | 6 | 7 | ✓ | ✓ | ✓ |
| pdf-031 | pdf | 6 | 0 | 3 | ✓ | ✓ | ✓ |
| pdf-032 | pdf | 148 | 149 | 147 | ✓ | ✓ | ✓ |
| pdf-033 | pdf | 101 | 142 | 65 | ✓ | ✓ | ✓ |
| pdf-034 | pdf | 14 | 8 | 16 | ✓ | ✓ | ✓ |
| syn-docx-01 | docx | 93 | 98 | 105 | ✓ | ✓ | ✓ |
| syn-docx-02 | docx | 56 | 60 | 81 | ✓ | ✓ | ✓ |
| syn-docx-03 | docx | 22 | 23 | 23 | ✓ | ✓ | ✓ |
| syn-docx-04 | docx | 29 | 32 | 32 | ✓ | ✓ | ✓ |
| syn-docx-05 | docx | 61 | 66 | 68 | ✓ | ✓ | ✓ |
| syn-docx-06 | docx | 52 | 390 | 390 | ✓ | ✓ | ✓ |
| syn-docx-07 | docx | 37 | 38 | 38 | ✓ | ✓ | ✓ |
| syn-docx-08 | docx | 70 | 76 | 78 | ✓ | ✓ | ✓ |
| syn-docx-09 | docx | 53 | 58 | 61 | ✓ | ✓ | ✓ |
| syn-docx-10 | docx | 6 | 6 | 6 | ✓ | ✓ | ✓ |
| syn-xlsx-01 | xlsx | 80 | 83 | 111 | ✓ | ✓ | ✓ |
| syn-xlsx-02 | xlsx | 64 | 66 | 82 | ✓ | ✓ | ✓ |
| syn-xlsx-03 | xlsx | 23 | 30 | 33 | ✓ | ✓ | ✓ |
| syn-xlsx-04 | xlsx | 77 | 84 | 128 | ✓ | ✓ | ✓ |
| syn-xlsx-05 | xlsx | 251 | 254 | 350 | ✓ | ✓ | ✓ |
| syn-xlsx-06 | xlsx | 69 | 69 | 96 | ✓ | ✓ | ✓ |
| syn-xlsx-07 | xlsx | 57 | 57 | 73 | ✓ | ✓ | ✓ |
| syn-xlsx-08 | xlsx | 32 | 33 | 46 | ✓ | ✓ | ✓ |
| syn-xlsx-09 | xlsx | 52 | 52 | 61 | ✓ | ✓ | ✓ |
| syn-xlsx-10 | xlsx | 10 | 11 | 10 | ✓ | ✓ | ✓ |
| syn-pptx-01 | pptx | 49 | 61 | 50 | ✓ | ✓ | ✓ |
| syn-pptx-02 | pptx | 53 | 87 | 60 | ✓ | ✓ | ✓ |
| syn-pptx-03 | pptx | 31 | 38 | 54 | ✓ | ✓ | ✓ |
| syn-pptx-04 | pptx | 34 | 38 | 35 | ✓ | ✓ | ✓ |
| syn-pptx-05 | pptx | 9 | 23 | 12 | ✓ | ✓ | ✓ |
| syn-pptx-06 | pptx | 20 | 41 | 24 | ✓ | ✓ | ✓ |
| syn-pptx-07 | pptx | 75 | 79 | 76 | ✓ | ✓ | ✓ |
| syn-pptx-08 | pptx | 7 | 13 | 7 | ✓ | ✓ | ✓ |
| syn-pptx-09 | pptx | 21 | 42 | 24 | ✓ | ✓ | ✓ |
| syn-pptx-10 | pptx | 229 | 273 | 237 | ✓ | ✓ | ✓ |
| syn-html-01 | html | 108 | 111 | 120 | ✓ | ✓ | ✓ |
| syn-html-02 | html | 49 | 50 | 80 | ✓ | ✓ | ✓ |
| syn-html-03 | html | 45 | 49 | 50 | ✓ | ✓ | ✓ |
| syn-html-04 | html | 25 | 27 | 27 | ✓ | ✓ | ✓ |
| syn-html-05 | html | 27 | 30 | 30 | ✓ | ✓ | ✓ |
| syn-html-06 | html | 20 | 21 | 21 | ✓ | ✓ | ✓ |
| syn-html-07 | html | 64 | 69 | 70 | ✓ | ✓ | ✓ |
| syn-html-08 | html | 31 | 81 | 80 | ✓ | ✓ | ✓ |
| syn-html-09 | html | 0 | 0 | 0 | ✓ | ✓ | ✓ |
| syn-html-10 | html | 2 | 2 | 2 | ✓ | ✓ | ✓ |
| syn-csv-01 | csv | 103 | 92 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-csv-02 | csv | 46 | 35 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-csv-03 | csv | 35 | 26 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-csv-04 | csv | 448 | 437 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-csv-05 | csv | 30 | 19 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-csv-06 | csv | 58 | 48 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-csv-07 | csv | 63 | 52 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-csv-08 | csv | 58 | 48 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-csv-09 | csv | 27 | 17 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-csv-10 | csv | 19 | 8 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-01 | json | 460 | 297 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-02 | json | 186 | 87 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-03 | json | 294 | 214 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-04 | json | 82 | 40 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-05 | json | 150 | 85 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-06 | json | 143 | 73 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-07 | json | 426 | 345 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-08 | json | 132 | 60 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-09 | json | 9 | 0 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-json-10 | json | 33 | 12 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-01 | xml | 85 | 157 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-02 | xml | 49 | 129 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-03 | xml | 56 | 100 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-04 | xml | 92 | 69 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-05 | xml | 34 | 61 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-06 | xml | 60 | 39 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-07 | xml | 27 | 89 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-08 | xml | 104 | 64 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-09 | xml | 18 | 35 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-xml-10 | xml | 14 | 18 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-01 | txt | 173 | 175 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-02 | txt | 129 | 131 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-03 | txt | 46 | 48 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-04 | txt | 66 | 708 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-05 | txt | 55 | 58 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-06 | txt | 6 | 7 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-07 | txt | 64 | 77 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-08 | txt | 74 | 83 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-09 | txt | 68 | 70 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-txt-10 | txt | 40 | 43 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-01 | md | 181 | 186 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-02 | md | 63 | 66 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-03 | md | 62 | 75 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-04 | md | 28 | 34 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-05 | md | 72 | 73 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-06 | md | 43 | 53 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-07 | md | 64 | 109 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-08 | md | 78 | 62 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-09 | md | 8 | 9 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-md-10 | md | 72 | 419 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-01 | zip | 143 | 141 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-02 | zip | 60 | 47 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-03 | zip | 116 | 84 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-04 | zip | 191 | 139 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-05 | zip | 97 | 71 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-06 | zip | 1,311 | 1,295 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-07 | zip | 150 | 117 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-08 | zip | 131 | 104 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-09 | zip | 80 | 60 | — (unsupported) | ✓ | ✓ | unsupported |
| syn-zip-10 | zip | 48 | 35 | — (unsupported) | ✓ | ✓ | unsupported |

## Failures

**pdf-005** (pdf) — aksharamd: error
  - `PARSE_FAILED`: PDF is password-protected — provide a decrypted copy.
**pdf-005** (pdf) — markitdown: exception
  - File conversion failed after 1 attempts:
 - PdfConverter threw PDFPasswordIncorrect with message: 

**pdf-005** (pdf) — docling: exception
  - Conversion failed for: libreoffice-writer-password.pdf with status: failure. Errors: docling-parse could not load document 3e333bff0196d0c5320f40cdd1b7a3abd21b316de79de3c0f9083accdaef9358: Failed to l
**pdf-019** (pdf) — docling: exception
  - Conversion failed for: unreadablemetadata.pdf with status: failure. Errors: docling-parse could not load document 9900c4c7edaa0b950314f33d8946b1247e3eafeea893958be76e62a32e1cd38a: Failed to load docum

## Reproducibility

```
python benchmarks/build_public_corpus.py
python benchmarks/run_comparison_benchmark.py
```

PDF corpus: https://github.com/py-pdf/sample-files (CC-BY-SA-4.0).
Synthetic files generated locally with no external dependencies.