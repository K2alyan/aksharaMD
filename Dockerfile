# ── Stage 1: build ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system deps needed to build Python packages with native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

# Install only production extras (ocr/audio are optional; install full set here
# so the runtime stage can copy a complete site-packages).
RUN pip install --no-cache-dir ".[ocr]" pytesseract openai-whisper

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# System runtime deps: Tesseract OCR, ffmpeg for audio, PDF rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only the installed Python packages from the builder — no dev tools
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Copy application source and install as non-editable package
COPY aksharamd/ aksharamd/
COPY pyproject.toml ./
RUN pip install --no-cache-dir --no-deps .

# Non-root user — required for production security
RUN useradd --uid 1001 --no-create-home --shell /sbin/nologin aksharamd

# Data directory that compile_document is allowed to read from
RUN mkdir -p /data && chown aksharamd:aksharamd /data

# Ledger directory for the non-root user
RUN mkdir -p /home/aksharamd/.aksharamd && chown aksharamd:aksharamd /home/aksharamd/.aksharamd

USER aksharamd

# Configurable port; health check reads the same env var
ENV AKSHARAMD_PORT=8000
ENV AKSHARAMD_ALLOWED_ROOT=/data

EXPOSE ${AKSHARAMD_PORT}

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c \
        "import socket, os; \
         port = int(os.environ.get('AKSHARAMD_PORT', 8000)); \
         socket.create_connection(('localhost', port), 2).close()"

CMD ["sh", "-c", "aksharamd-mcp --transport streamable-http --host 0.0.0.0 --port ${AKSHARAMD_PORT}"]
