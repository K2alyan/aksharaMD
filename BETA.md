# AksharaMD Beta Testing Guide

Thank you for testing AksharaMD. This guide tells you exactly what to install, what to try, and what to report back.

---

## What You Are Testing

AksharaMD converts documents (PDFs, Word files, spreadsheets, images, audio, and 40+ other formats) into clean, token-efficient Markdown that can be fed directly to any LLM. The core claim is that it produces significantly fewer tokens than alternatives while preserving all the meaningful structure.

There are two interfaces to test:

1. **CLI** — run from your terminal, compile documents directly
2. **MCP** — connect AksharaMD to Claude Desktop so Claude can compile documents on demand during a conversation

---

## Part 1: Installation

**Requirements:** Python 3.11 or later

```bash
# Standard install
pip install aksharamd

# Confirm it works
aksharamd --version
aksharamd formats
```

`aksharamd formats` should print a table of 40+ supported file types. If it does, you are good to go.

**Optional extras** (install only if you want to test those formats):

```bash
# Image OCR — also requires Tesseract installed on your system
pip install "aksharamd[ocr]"

# Audio transcription — also requires ffmpeg on your PATH
pip install "aksharamd[audio]"
```

---

## Part 2: CLI Testing

### 2a. Basic compilation

Pick any document you have on hand and run:

```bash
aksharamd compile /path/to/your/file.pdf
```

Output goes to `output/<filename>/`:
- `document.md` — the compiled Markdown
- `manifest.json` — token counts, timing, readiness score
- `validation.json` — any extraction warnings

Open `document.md` and check:
- Are headings preserved correctly?
- Are tables readable?
- Is important content missing?
- Does the output make sense to you as someone who knows the source document?

### 2b. Test across formats

Try at least 3–4 different file types from this list. Pick formats you actually use:

| Format | Command |
|--------|---------|
| PDF | `aksharamd compile file.pdf` |
| Word document | `aksharamd compile file.docx` |
| PowerPoint | `aksharamd compile file.pptx` |
| Excel spreadsheet | `aksharamd compile file.xlsx` |
| HTML page | `aksharamd compile file.html` |
| CSV data file | `aksharamd compile data.csv` |
| JSON file | `aksharamd compile data.json` |
| Markdown | `aksharamd compile notes.md` |
| ZIP archive | `aksharamd compile archive.zip` |
| Image (needs OCR) | `aksharamd compile scan.png` |
| Audio (needs Whisper) | `aksharamd compile recording.mp3` |

### 2c. Check the readiness score

After each compilation, run:

```bash
aksharamd compile file.pdf --show-manifest
```

You will see a confidence score (0–100) and notes explaining it. Ask yourself:
- Does the score feel accurate for this document?
- If the score is high (85+) but the output looks bad, that is a bug — report it.
- If the score is low but the output looks fine, also report it.

### 2d. Check token savings

```bash
aksharamd stats
```

This shows cumulative token savings across all your compilations. After testing several files, this should show meaningful savings compared to the raw document sizes.

### 2e. Validate output

```bash
aksharamd validate file.pdf
```

This runs the extraction pipeline and reports any structural issues without writing output files. Useful for quickly checking if a file will compile cleanly.

---

## Part 3: MCP Testing (Claude Desktop Integration)

This connects AksharaMD to Claude Desktop so you can ask Claude to compile documents during a conversation.

### 3a. Set up the MCP connection

```bash
aksharamd mcp-config --write
```

This auto-writes the configuration to Claude Desktop's config file. Then **restart Claude Desktop completely** (quit and reopen, not just close the window).

### 3b. Verify the connection

Open a new conversation in Claude Desktop. Type:

> "What document formats can AksharaMD compile?"

Claude should invoke the `get_supported_formats` tool and return a list of formats. If you see no tool use, the MCP is not connected — check that you restarted Claude Desktop after running `mcp-config --write`.

### 3c. Compile a document through Claude

In Claude Desktop, try:

> "Use AksharaMD to compile this file: /full/path/to/your/document.pdf"

Use the **full absolute path** to the file. Claude will call `compile_document` and return the compiled Markdown inline in the conversation.

Things to check:
- Does the output look correct?
- Is the savings summary at the bottom accurate?
- Does Claude seem to understand the document content based on the compiled output?

### 3d. Ask questions about the compiled document

After compiling, try asking Claude questions about the document's content. For example:

> "Based on the document you just compiled, what are the main conclusions?"

This is the core use case — compile a document, then reason about it. Check whether Claude's answers are accurate given the source document.

### 3e. Test the MCP Inspector (optional, for technical testers)

```bash
npx @modelcontextprotocol/inspector python -m aksharamd.mcp_server
```

Open the URL it prints. In the Tools tab, you can call each tool directly:
- `get_supported_formats` — no input needed
- `get_stats` — no input needed
- `compile_document` — enter a file path **without surrounding quotes**
- `compile_document_multimodal` — same, returns text + embedded images

---

## Part 4: Edge Cases to Try

These are specifically useful to test:

| Scenario | How to test |
|----------|-------------|
| Scanned PDF (image-only) | Compile a PDF that has no selectable text |
| Password-protected PDF | Compile a PDF that requires a password |
| Very large file (50+ MB) | Compile a large PDF or archive |
| Corrupt or truncated file | Rename a random binary file to `.pdf` and compile it |
| File with mixed languages | Compile a document that has non-English text |
| ZIP with many files | Compile a ZIP containing 50+ files |
| Empty file | Create an empty `.txt` and compile it |
| Image with no text | Compile a photo or diagram (requires OCR install) |

For each, note what happened — did it fail gracefully with a clear message, or did it crash?

---

## What to Report

For every issue or observation, please tell us:

**Required:**
1. What file type / format were you testing?
2. What did you do (exact command or what you said to Claude)?
3. What did you expect to happen?
4. What actually happened?

**Helpful extras:**
- The confidence score and notes from the manifest
- Whether the output was missing content that was in the original
- Whether the output had extra noise or garbage that should not be there
- How long the compilation took (shown in the manifest)
- Your OS and Python version (`python --version`)

**Specific things we want to know:**
- Cases where the confidence score felt wrong (too high or too low)
- Formats that produced garbled or fragmented output
- Any crash or unhandled error (paste the full error message)
- Files where token savings were zero or negative — was the output actually not cleaned up?
- MCP connection issues or tool call failures
- Performance: anything that took more than 30 seconds for a normal-sized document

---

## How to Submit Feedback

Open an issue at: **https://github.com/K2alyan/aksharaMD/issues**

Use this template:

```
**Format tested:** PDF / DOCX / etc.
**OS:** Windows 11 / macOS 14 / Ubuntu 22.04
**Python version:** 3.11.x
**Interface:** CLI / Claude Desktop MCP / MCP Inspector

**What I did:**
aksharamd compile myfile.pdf

**What I expected:**
Clean Markdown with headings and tables preserved

**What happened:**
[paste output or error here]

**Confidence score:** 72/100
**Notes from manifest:** [paste the notes lines]
```

---

## Quick Reference

```bash
# Compile a file
aksharamd compile file.pdf

# Compile and show the manifest inline
aksharamd compile file.pdf --show-manifest

# Validate without writing output
aksharamd validate file.pdf

# See all supported formats
aksharamd formats

# Check lifetime token savings
aksharamd stats

# Set up Claude Desktop MCP connection
aksharamd mcp-config --write

# Get help on any command
aksharamd compile --help
```

---

Thank you for your time. Every bug report and piece of feedback directly improves the tool.
