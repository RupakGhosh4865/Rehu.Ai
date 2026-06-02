# Savant.ai — Railway production image
# Build context: repository root (SuperHuman-Platform/)

FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY frontend/ /app/frontend

ENV PYTHONDONTWRITEBYTECODE=1
ENV CHROMA_PERSIST_DIR=/app/backend/data/chromadb
ENV PYTHONPATH=/app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "import os, httpx; httpx.get(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=8)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
