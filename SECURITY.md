# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest release on PyPI | Yes |
| Older releases | No — please upgrade |

Security fixes are applied to the current release only. We do not backport patches to older versions.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report vulnerabilities privately by emailing **ksrkklabs@gmail.com** with the subject line `[AksharaMD Security]`. Include:

- A description of the vulnerability and its potential impact
- Steps to reproduce or a minimal proof-of-concept
- Affected version(s) and environment details

You will receive an acknowledgement within 72 hours. We aim to release a patch within 14 days for critical issues and 30 days for moderate issues.

We appreciate responsible disclosure and will credit reporters in the changelog unless you prefer to remain anonymous.

## Security Scope

AksharaMD processes **untrusted documents from arbitrary sources**. The following attack surfaces are explicitly in scope:

### In scope

- **Archive safety** — ZIP/TAR path traversal, ZIP bombs, decompression-ratio attacks, recursive archive extraction, ZIP entry count exhaustion
- **XML/HTML injection** — entity expansion (XXE), malformed XML, hostile HTML that attempts to reach local resources via `file://` or `data:` URIs
- **PDF parser attacks** — malformed PDFs designed to crash the parser, PDFs with hostile embedded content, invisible-text injection
- **SSRF via URL input** — requests to internal/private IP ranges when `aksharamd compile https://...` is used
- **Path traversal** — any parser that extracts files to disk or resolves relative paths to assets
- **Dependency vulnerabilities** — CVEs in `pymupdf`, `pydantic`, `python-docx`, `defusedxml`, or other direct dependencies
- **Command injection** — environment variables or document metadata used to construct system calls

### Out of scope

- Vulnerabilities in optional extras (`[vision]`, `[math]`, `[audio]`) that require an attacker to already control the model weights or the ML framework
- Vulnerabilities in documents that are processed **and** the output is intentionally passed to a downstream system the attacker also controls
- Denial-of-service via extremely large but otherwise valid documents (use `AKSHARAMD_MAX_FILE_BYTES` and `AKSHARAMD_MAX_ARCHIVE_BYTES` environment variables to set limits)

## Security Controls

Current protections implemented in the codebase:

- **SSRF protection** — URL fetch resolves the hostname and rejects RFC 1918 / loopback / link-local addresses before opening a connection
- **Archive limits** — ZIP/TAR parsers enforce maximum entry count (`_MAX_ZIP_ENTRIES`) and maximum decompressed size (`_MAX_ARCHIVE_DECOMPRESSED_BYTES`) before extraction
- **ZIP path traversal** — entry names containing `../` components are blocked
- **HTML asset isolation** — image resolution is restricted to files within the document's own directory; symlinks and `../` paths are blocked; remote URLs are not fetched
- **File size gate** — files exceeding `AKSHARAMD_MAX_FILE_BYTES` (default 500 MB) are rejected before parsing
- **XML safety** — `defusedxml` is used for all XML parsing to prevent entity expansion attacks
- **Whisper model whitelist** — the `AKSHARAMD_WHISPER_MODEL` environment variable is validated against an allowlist to prevent command injection

## Security Model by Deployment Mode

AksharaMD has three distinct deployment surfaces with different trust boundaries.

### Local CLI (`aksharamd compile …`)

- Processes files from the local filesystem only. No network access during compilation unless the source is an explicit `http://`, `https://`, or `s3://` URL.
- Output is written to the local filesystem. Nothing is uploaded or transmitted.
- Runs in the same user process with the same file permissions as the caller. There is no sandbox between the parser and the calling process.
- **Threat model:** the adversary controls the document content, not the process. Controls are applied at parse time (archive limits, size gate, XML safety, HTML asset isolation).

### MCP server (`aksharamd-mcp`)

Two transport modes with different trust levels:

**stdio mode (default, used by Claude Desktop):**
- The MCP server is launched as a child process by the host application.
- No network listener is opened; communication is over stdin/stdout pipes.
- No authentication is required — the host controls process launch.
- File access is unrestricted unless `AKSHARAMD_ALLOWED_ROOT` is set. In personal use with Claude Desktop this is acceptable. In shared environments, set `AKSHARAMD_ALLOWED_ROOT` to the documents directory.

**HTTP mode (`--transport streamable-http`):**
- Opens a network listener. Any client that can reach the port can send requests.
- Set `AKSHARAMD_MCP_API_KEY` to require an `X-API-Key` header on every request.
- Set `AKSHARAMD_ALLOWED_ROOT` to restrict which directories the server will read. Without this, any authenticated client can request any file readable by the server process.
- Set `AKSHARAMD_MAX_BODY_BYTES` (default 1 MB) to limit request body size.
- **Do not run HTTP mode on a public interface without both `AKSHARAMD_MCP_API_KEY` and `AKSHARAMD_ALLOWED_ROOT` set.**

### Indexing mode (`[index]` extra)

The `[index]` extra adds a local vector index backed by ChromaDB.

- **Local storage:** the index is stored on disk at `~/.aksharamd/index/` by default. It is not synced to any remote service. No document content leaves the machine.
- **Embedding model downloads:** on first use, `sentence-transformers` downloads `all-MiniLM-L6-v2` (~90 MB) from HuggingFace. Subsequent runs use the cached copy. The download URL is `https://huggingface.co`. If outbound traffic to HuggingFace is blocked, set `SENTENCE_TRANSFORMERS_HOME` to a pre-populated model cache directory.
- **Embedding space enforcement:** opening an existing index with a different embedding model or vector dimension raises `EmbeddingConfigMismatch`. This prevents silently mixing vectors from incompatible embedding spaces, which would corrupt retrieval results without a visible error.
- **No LLM calls:** indexing and retrieval are pure embedding + vector search operations. No LLM API calls are made during indexing. If you connect the index to an LLM for query answering (Ollama, OpenAI, etc.), those calls are governed by your own pipeline code — AksharaMD does not make them.

## Readiness Score and Acceptance Threshold

The default acceptance threshold for indexing is **70/100** (the start of the OK band: HIGH ≥ 85, OK ≥ 70, RISKY ≥ 50, POOR < 50).

What this means in practice:

- **≥ 70 (OK/HIGH):** the document's text layer was extracted with sufficient structure and density for reliable embedding. The parser found recognizable headings, paragraphs, or table structure.
- **< 70 (RISKY/POOR):** the parser detected significant problems — missing text layer, OCR failures, repetitive content, glyph artifacts, or very low token density. These documents may produce misleading embeddings because the text content is incomplete or unreliable.

The threshold is a heuristic, not a guarantee. A score of 70 means the extraction *appeared* clean by structural and density signals; it does not certify that all semantic content was recovered. Calibration data linking score bands to empirical recall rates is planned for v0.5.0.

Override the threshold with `--min-readiness-score` (CLI) or `min_readiness_score` in `IndexConfig` (Python API). Set to 0 to index everything regardless of quality; set to 85 to index only HIGH-band documents.

## Deferred Dependency Alerts

The following Dependabot vulnerability alerts are present in the lockfile but **cannot currently be resolved** because upstream optional-extra dependencies impose version caps that conflict with the fixed package versions. No code change in this repository can fix them until the upstream packages release new versions.

### pillow — 5 alerts (HIGH × 3, MEDIUM × 2)

**Fixed version required:** ≥ 12.2.0  
**Locked version:** 10.4.0  
**Blocker:** `surya-ocr` (required by `marker-pdf`, which powers the `[vision]` extra) declares `pillow < 11.0.0` across every released version (0.1.0–0.20.0). Because uv builds a universal lockfile that resolves all extras simultaneously, this cap pins `pillow` to 10.4.0 even for base-install users.

**Advisories:**
- OOB write when loading PSD images (× 2)
- FITS GZIP decompression bomb (DoS)
- PDF parsing trailer infinite loop (DoS)
- Integer overflow in font processing

**Practical exposure:** AksharaMD uses `pillow` for general image handling during document parsing. It does not process PSD or FITS files in normal ingestion workflows. The PDF trailer DoS applies to pillow's own PDF parser, not to PyMuPDF which AksharaMD uses for PDF parsing. Risk is low in practice for the documented use case but the installed package remains vulnerable.

**Removal condition:** When `surya-ocr` releases a version that supports `pillow >= 12.0`, remove the `pillow` ignore rule from `.github/dependabot.yml`, bump `Pillow >= 12.2.0` in `pyproject.toml`, regenerate `uv.lock`, and close the alerts.  
**Track:** https://github.com/VikParuchuri/surya

---

### transformers — LightGlue arbitrary code execution (CVE-2026-5241)

**Advisory:** GHSA-fgcw-684q-jj6r / CVE-2026-5241 / PYSEC-2026-2290 (High, CVSS 8.0, CWE-829)  
**Fixed version required:** ≥ 5.5.0 (no 4.x back-port exists; the fix is in 5.5.0 only)  
**Locked version:** 4.57.6 (in the affected range `< 5.5.0`)  
**Blocker:** `marker-pdf >= 1.6` declares `transformers < 5.0.0` across all released versions (1.6.0–1.10.2). Affects the `[vision]` and `[math]` optional extras only. The base install does not depend on `transformers`.

**Vulnerable code path:** `transformers.models.lightglue.configuration_lightglue.LightGlueConfig` reads `trust_remote_code` from the untrusted `config.json` of the target model repository and forwards it to `AutoConfig.from_pretrained()` for the sub-model configs. As a result, an attacker who controls the model repository can execute arbitrary Python code even when the caller passes `trust_remote_code=False`.

**Practical exposure in AksharaMD: none.** Evidence-based reachability review 2026-07-17, refreshed 2026-07-21 for the Unlimited-OCR production relocation (PR 93):

- `aksharamd/` production source references `LightGlue`, `AutoModel`, `AutoConfig`, or `trust_remote_code` in exactly TWO files, both allowlisted in `tests/test_security_transformers_reachability.py::_ALLOWED_PATHS`:
  - `aksharamd/plugins/ocr_backends/unlimited_ocr/adapter.py` — Unlimited-OCR pinned model loader.
  - `aksharamd/plugins/ocr_backends/eval_override.py` — audited module-local eval override for that specific model's remote-code surface.
- Both are gated by a byte-level trust manifest (`unlimited_ocr_trusted_manifest.json`) verified against the pinned revision `d549bb9d6a055dbe291408916d66acc2cd5920f6` of `baidu/Unlimited-OCR` before any load. The model repo id is a hardcoded constant; **no user input reaches the model-id argument**.
- Full static review of the trusted code lives at `docs/security/unlimited_ocr_static_review_d549bb9d.md`. The eval-override sandbox restricts what the remote code may do, and is unit-tested in `tests/test_unlimited_ocr_eval_override.py`.
- Unlimited-OCR is NOT wired into the default compile flow (PR 94 will add explicit opt-in `--ocr-backend unlimited_ocr` selection). Base installs do not import `torch` or `transformers` at package load — enforced by `tests/test_unlimited_ocr_no_heavy_import.py`.
- `marker-pdf 1.10.2` (installed) has 0 references across 130 `.py` files.
- `surya-ocr 0.17.1` (installed) has 0 references across 87 `.py` files.
- The only marker entry point is `marker.models.create_model_dict()` called with **no arguments**; every downstream checkpoint is a hardcoded Surya-package constant. No user input reaches any `from_pretrained` call anywhere in the marker pipeline.
- Dynamic verification: instantiating marker's full model dict loads 40 `transformers.*` submodules; **zero** are `lightglue.*`.
- The `trust_remote_code=True` in `benchmarks/docvqa_eval.py:124` is for HuggingFace **Datasets** (not `transformers`), targets a well-known dataset, and is annotated `# nosec B615`.

Effective severity for AksharaMD: **informational**. The vulnerable code is present on disk in a transitive dependency but no code path in this repository reaches it with any attacker-controllable model repository id. The two allowlisted production references target a pinned, byte-verified, statically-reviewed model whose repository id is a compile-time constant.

**Do not silence:** Do not attempt to hide this alert with a `pyproject.toml` version-range trick or an ignore rule for CVE-2026-5241 specifically. Either would mask a future transitive bump that could unlock the vulnerable path. The `>=5.0.0` ignore rule in `.github/dependabot.yml` is a different, coarse block on incompatible major-version bumps and remains appropriate.

**Removal condition:** When `marker-pdf` releases a version that supports `transformers >= 5.5.0`, open a coordinated bump PR that (1) lifts the `<5.0.0` cap in `pyproject.toml` `[vision]`, (2) sets a floor of `transformers >= 5.5.0`, (3) removes the `transformers >=5.0.0` ignore in `.github/dependabot.yml`, (4) regenerates `uv.lock`, and (5) closes this alert.  
**Track:** https://github.com/VikParuchuri/marker
