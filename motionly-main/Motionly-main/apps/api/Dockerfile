FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=2 \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/app ./app
COPY apps/api/worker-bridge ./apps/api/worker-bridge

RUN cd apps/api/worker-bridge \
    && npm install --omit=dev --no-audit --no-fund --no-save bullmq@5.78.0 ioredis@5.11.1 \
    && cd /app \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 motionly \
    && chown -R motionly:motionly /app

USER motionly

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
