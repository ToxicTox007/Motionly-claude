FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

4RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg fonts-dejavu-core nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/app ./app
COPY apps/worker/package.json ./apps/worker/package.json
COPY apps/worker/src/queue-common.mjs ./apps/worker/src/queue-common.mjs
COPY apps/worker/src/queue-bridge.mjs ./apps/worker/src/queue-bridge.mjs

RUN cd apps/worker && npm install --omit=dev --no-audit --no-fund

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
