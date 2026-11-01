FROM python:3.12-slim@sha256:e8a38829209aea82774882e5157e1f7c3e8c8393ff53a697b3580896e57f345c

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=2 \
    PORT=8000

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Node.js 24 LTS from the pinned official image (Decision D1); Debian's
# nodejs package is Node 20, which is past end of life.
COPY --from=node:24-bookworm-slim@sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-bookworm-slim@sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global npm@12.2.1 --ignore-scripts --no-audit --no-fund \
    && node --version && npm --version

WORKDIR /app

COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/requirements.lock ./requirements.lock
COPY apps/api/app ./app
COPY apps/api/worker-bridge/package.json ./apps/api/worker-bridge/package.json
COPY apps/api/worker-bridge/package-lock.json ./apps/api/worker-bridge/package-lock.json
COPY apps/worker/src/queue-bridge.mjs ./apps/api/worker-bridge/queue-bridge.mjs
COPY apps/worker/src/queue-common.mjs ./apps/api/worker-bridge/queue-common.mjs

# npm only installs the locked Node packages at build time. Remove it so the image
# ships no package manager (npm bundles its own HTTP client, undici, which the
# supply-chain image scan flags and nothing here runs).
RUN cd apps/api/worker-bridge \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx \
    && cd /app \
    && pip install --no-cache-dir --require-hashes -r requirements.lock \
    && useradd --create-home --uid 10001 motionly \
    && mkdir -p /var/data/motionly /var/cache/motionly /tmp/motionly \
    && chown -R motionly:motionly \
        /app /var/data/motionly /var/cache/motionly /tmp/motionly

USER motionly

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
