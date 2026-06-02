# Savant.ai — Backend Dockerfile
# Optimised for Railway.app deployment

FROM python:3.11-slim

# System deps for audio processing, chromadb, and the ride-along Playwright bot
RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    libssl-dev \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python dependencies first (better Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ride-along meeting bot — install Chromium + its OS dependencies
# (apt deps are pulled in by `playwright install-deps`).
RUN playwright install chromium && playwright install-deps chromium

# Pre-download the embedding model so it's baked into the image
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" || true

# Copy application code
COPY . .

# Copy frontend when building from backend/ context (local docker-compose)
COPY frontend/ /app/frontend

# Expose port (Railway uses PORT env var)
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health')"

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
