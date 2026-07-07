# AksharaMD Multi-Tool Comparison Benchmark

**Generated:** 2026-07-07 23:01 UTC  
**Total files:** 20  
**Elapsed:** 21.9s  
**Tools:** aksharamd, markitdown  

## Overall Summary

| Metric | Aksharamd | Markitdown |
| --- | --- | --- |
| Files attempted | 20 | 20 |
| Succeeded | 10 | 10 |
| Success rate | 50% | 50% |
| Avg tokens (success) | 147 | 140 |
| Avg chars (success) | 591 | 561 |
| Avg elapsed | 0.18s | 0.48s |

## Token Efficiency vs AksharaMD

| Format | Aksharamd tokens | Markitdown tokens | Ratio (AksharaMD÷other) |
| --- | --- | --- | --- |
| csv | 103 | 92 | 0.9× |
| docx | 93 | 98 | 1.1× |
| html | 108 | 111 | 1.0× |
| json | 460 | 297 | 0.6× |
| md | 181 | 186 | 1.0× |
| pdf | - | - | - |
| pptx | 49 | 61 | 1.2× |
| txt | 173 | 175 | 1.0× |
| xlsx | 80 | 83 | 1.0× |
| xml | 85 | 157 | 1.8× |
| zip | 143 | 141 | 1.0× |

## Per-File Results

| ID | Format | Aksharamd tokens | Markitdown tokens | Aksharamd ok? | Markitdown ok? |
| --- | --- | --- | --- | --- | --- |
| pdf-001 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| pdf-002 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| pdf-004 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| pdf-006 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| pdf-010 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| pdf-011 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| pdf-016 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| pdf-023 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| pdf-025 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| pdf-031 | pdf | — (skipped) | — (skipped) | skipped | skipped |
| syn-docx-01 | docx | 93 | 98 | ✓ | ✓ |
| syn-xlsx-01 | xlsx | 80 | 83 | ✓ | ✓ |
| syn-pptx-01 | pptx | 49 | 61 | ✓ | ✓ |
| syn-html-01 | html | 108 | 111 | ✓ | ✓ |
| syn-csv-01 | csv | 103 | 92 | ✓ | ✓ |
| syn-json-01 | json | 460 | 297 | ✓ | ✓ |
| syn-xml-01 | xml | 85 | 157 | ✓ | ✓ |
| syn-txt-01 | txt | 173 | 175 | ✓ | ✓ |
| syn-md-01 | md | 181 | 186 | ✓ | ✓ |
| syn-zip-01 | zip | 143 | 141 | ✓ | ✓ |

## Reproducibility

```
python benchmarks/build_public_corpus.py
python benchmarks/run_comparison_benchmark.py
```

PDF corpus: https://github.com/py-pdf/sample-files (CC-BY-SA-4.0).
Synthetic files generated locally with no external dependencies.