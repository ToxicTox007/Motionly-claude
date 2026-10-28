FROM node:24-bookworm-slim@sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae AS build
RUN npm install --global npm@12.2.1 --ignore-scripts --no-audit --no-fund
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY packages/shared ./packages/shared
COPY apps/web ./apps/web
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @motionly/shared build && pnpm --filter @motionly/web build

# Runtime: only the standalone server (server.js and the node_modules files it
# uses, traced by `next build` from the monorepo root), the static assets and
# public/. No workspace sources, dev dependencies or package manager.
FROM node:24-bookworm-slim@sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae
RUN npm install --global npm@12.2.1 --ignore-scripts --no-audit --no-fund
WORKDIR /app
# server.js listens on HOSTNAME, which Docker sets to the container id; listen
# on every interface (the health check uses 127.0.0.1).
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    HOME=/home/node \
    XDG_CACHE_HOME=/home/node/.cache \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /app/apps/web/public ./apps/web/public
RUN mkdir -p /app/apps/web/.next/cache /home/node/.cache /tmp/motionly \
    && chown -R node:node /home/node /app/apps/web/.next /tmp/motionly
USER node
EXPOSE 3000
# Start the server directly with node: the production network has no egress,
# and a package manager shim (Corepack) would try to download pnpm at boot.
# It exits non-zero when its runtime configuration is invalid
# (apps/web/instrumentation.ts).
WORKDIR /app
CMD ["node", "apps/web/server.js"]
