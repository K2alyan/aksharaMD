FROM python:3.11-slim

# System deps: OCR, audio transcription, PDF rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps before copying source for better layer caching
COPY pyproject.toml ./
RUN pip install --no-cache-dir ".[dev]" \
    && pip install --no-cache-dir pytesseract openai-whisper

# Copy application source
COPY aksharamd/ aksharamd/

# Install the package itself (non-editable for production)
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["aksharamd-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
