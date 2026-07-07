# Contributing to AksharaMD

This guide covers how to test AksharaMD, what makes a useful bug report, and how to submit one.

---

## Installation check

```bash
pip install aksharamd
aksharamd --version
aksharamd formats
```

`aksharamd formats` should print a table of 40+ supported file types. If it does, the install is working.

**Optional extras** (install only what you need):

```bash
pip install "aksharamd[ocr]"    # scanned PDFs — also requires Tesseract on PATH
pip install "aksharamd[audio]"  # audio/video transcription — also requires ffmpeg on PATH
pip install "aksharamd[vision]" # image-based table reconstruction (neural, ~3 GB)
```

---

## Testing documents

### Basic compilation

```bash
aksharamd compile /path/to/your/file.pdf
```

Output goes to `output/<filename>/`:
- `document.md` — compiled Markdown
- `manifest.json` — token counts, timing, readiness score
- `validation.json` — extraction warnings

Open `document.md` and check: are headings preserved? Are tables readable? Is important content missing?

### Try multiple formats

Pick formats you actually use:

| Format | Command |
|--------|---------|
| PDF | `aksharamd compile file.pdf` |
| Word | `aksharamd compile file.docx` |
| PowerPoint | `aksharamd compile file.pptx` |
| Excel | `aksharamd compile file.xlsx` |
| HTML | `aksharamd compile file.html` |
| CSV | `aksharamd compile data.csv` |
| JSON | `aksharamd compile data.json` |
| Markdown | `aksharamd compile notes.md` |
| ZIP archive | `aksharamd compile archive.zip` |
| Image (needs OCR) | `aksharamd compile scan.png` |
| Audio (needs Whisper) | `aksharamd compile recording.mp3` |

### Check the readiness score

The compilation summary shows a confidence score (0–100) and quality band (HIGH / OK / RISKY / POOR).

If the score is HIGH but the output looks bad — that is a bug worth reporting.
If the score is LOW but the output looks fine — also report it.

### Machine-readable output (for scripting or CI)

```bash
aksharamd compile file.pdf --json
```

Prints a single JSON object: `success`, `readiness_score`, `quality_band`, `warning_codes`, `chunks`, `pages`, `optimized_tokens`, `elapsed_seconds`, and more.

### Ingestion gate (CI/CD)

```bash
aksharamd compile file.pdf --min-readiness-score 70
```

Exits non-zero if the readiness score is below 70. Output files are still written. Useful for blocking low-quality extractions from reaching a vector store.

### Token savings summary

```bash
aksharamd stats
```

Shows cumulative token savings across all compilations.

### Validate without writing output

```bash
aksharamd validate file.pdf
```

Runs the pipeline and reports structural issues without writing any files. Useful for quickly checking if a file will compile cleanly.

---

## Edge cases to try

| Scenario | How to test |
|----------|-------------|
| Scanned PDF (image-only) | Compile a PDF with no selectable text |
| Password-protected PDF | Compile a PDF that requires a password |
| Very large file (50+ MB) | Compile a large PDF or archive |
| Corrupt or truncated file | Rename a random binary file to `.pdf` and compile it |
| Mixed-language document | Compile something with non-English text |
| ZIP with many files | Compile a ZIP containing 50+ files |
| Empty file | Create an empty `.txt` and compile it |
| Image with no text | Compile a photo or diagram (requires OCR) |

For each: did it fail gracefully with a clear message, or did it crash?

---

## What makes a good bug report

**Required:**
1. What file type / format were you testing?
2. What command did you run (exact)?
3. What did you expect to happen?
4. What actually happened?

**Helpful extras:**
- The readiness score and quality band from the compilation summary
- Whether output was missing content from the source document
- Whether there was extra noise or garbage in the output
- How long compilation took
- Your OS and Python version (`python --version`)

**Things we specifically want to know:**
- Readiness score that felt wrong (too high or too low for the actual output quality)
- Formats that produced garbled or fragmented output
- Any crash or unhandled exception — paste the full traceback
- Files where token savings were zero or negative
- `--json` output that was not valid JSON or had wrong field values
- `--min-readiness-score` that did not exit with the expected code

---

## Bug report template

Open an issue at: **https://github.com/K2alyan/aksharaMD/issues**

```
**Format tested:** PDF / DOCX / etc.
**OS:** Windows 11 / macOS 14 / Ubuntu 22.04
**Python version:** 3.11.x
**AksharaMD version:** (aksharamd --version)

**Command:**
aksharamd compile myfile.pdf

**Expected:**
Clean Markdown with headings and tables preserved.

**What happened:**
[paste output or error here]

**Readiness score:** 72/100  OK
**Warning codes:** [paste from summary or --json output]
```

---

## Code contributions

Bug fixes and targeted improvements are welcome. Please open an issue first to discuss significant changes.

Before submitting a pull request:

```bash
# Lint
ruff check aksharamd/ tests/

# Type check
mypy aksharamd/ --ignore-missing-imports --no-error-summary

# Targeted tests (run these locally)
python -m pytest tests/test_cli.py tests/test_compiler.py -q --tb=short

# Security scan
bandit -r aksharamd/ -ll -q
```

Full CI runs on GitHub Actions on every PR. See [ADR.md](ADR.md) for the reasoning behind major architectural decisions.

---

## Quick reference

```bash
aksharamd compile file.pdf                        # compile a file
aksharamd compile file.pdf --json                 # machine-readable JSON output
aksharamd compile file.pdf --min-readiness-score 70  # fail if score < 70
aksharamd validate file.pdf                       # check without writing output
aksharamd formats                                 # list all supported file types
aksharamd stats                                   # lifetime token savings
aksharamd doctor                                  # check optional dependencies
aksharamd mcp-config --write                      # set up Claude Desktop MCP
aksharamd compile --help                          # help for any command
```
