# ─── Motionly Worker + Remotion Renderer ─────────────────────────────────────
# This container runs both the queue worker (apps/worker) and can spawn the
# Remotion renderer (apps/renderer) as a subprocess for premium renders.
#
# Chrome/Chromium is required by @remotion/renderer for headless frame capture.
# ─────────────────────────────────────────────────────────────────────────────

FROM node:20-bookworm-slim

# ── System dependencies ───────────────────────────────────────────────────────
# Install Chromium and all shared libraries it needs for headless rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        fonts-liberation \
        fonts-noto-color-emoji \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Tell Remotion / Puppeteer where Chrome lives
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
    REMOTION_DISABLE_SANDBOX=true \
    NODE_ENV=production

# ── Source code ───────────────────────────────────────────────────────────────
WORKDIR /app

# Copy workspace manifests first for better layer caching
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/shared/package.json ./packages/shared/
COPY apps/worker/package.json ./apps/worker/
COPY apps/renderer/package.json ./apps/renderer/

# Install pnpm and all workspace dependencies
RUN npm install -g pnpm@9 --quiet && \
    pnpm install --frozen-lockfile --prefer-offline --prod=false

# Copy source after deps (avoids reinstalling when only source changes)
COPY packages/shared ./packages/shared
COPY apps/worker ./apps/worker
COPY apps/renderer ./apps/renderer

# ── Pre-warm Remotion bundle ───────────────────────────────────────────────────
# Runs the webpack compilation once at build time so the first render request
# doesn't incur the ~30 second bundling cost. The bundle path is written to
# apps/renderer/.remotion-bundle and read by render-cli.ts at runtime.
#
# This step is skipped if SKIP_PREBUNDLE=true (useful for faster dev builds).
RUN if [ "${SKIP_PREBUNDLE}" != "true" ]; then \
        cd apps/renderer && \
        node --loader ts-node/esm src/prebundle.ts; \
    fi

# ── Runtime ───────────────────────────────────────────────────────────────────
WORKDIR /app/apps/worker

# Expose the renderer base path so remotion.mjs can find it
ENV RENDERER_BASE_PATH=/app/apps/renderer

CMD ["node", "src/worker.mjs", "--loop"]
