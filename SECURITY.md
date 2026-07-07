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

### transformers — 2 alerts (HIGH × 1, MEDIUM × 1)

**Fixed version required:** ≥ 5.3.0  
**Locked version:** 4.57.6  
**Blocker:** `marker-pdf >= 1.6` declares `transformers < 5.0.0` across all released versions (1.6.0–1.10.2). Affects the `[vision]` and `[math]` optional extras only. The base install does not depend on `transformers`.

**Advisories:**
- Remote code execution via the `Trainer` class
- Arbitrary code execution via pickle deserialization

**Practical exposure:** AksharaMD uses `transformers` (via `marker-pdf` / `surya-ocr`) exclusively for neural layout detection inference. The `Trainer` class is never instantiated. Pickle deserialization of untrusted model weights is not performed during normal document ingestion. Risk is low in practice but the installed package remains vulnerable when `[vision]` is installed.

**Removal condition:** When `marker-pdf` releases a version that supports `transformers >= 5.0`, remove the `transformers` ignore rule from `.github/dependabot.yml`, upgrade accordingly, regenerate `uv.lock`, and close the alerts.  
**Track:** https://github.com/VikParuchuri/marker
