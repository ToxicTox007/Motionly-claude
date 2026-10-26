FROM node:24-bookworm-slim@sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae AS build
RUN npm install --global npm@12.2.1 --ignore-scripts --no-audit --no-fund
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY packages/shared ./packages/shared
COPY apps/web ./apps/web
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @motionly/shared build && pnpm --filter @motionly/web build

FROM node:24-bookworm-slim@sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae
RUN npm install --global npm@12.2.1 --ignore-scripts --no-audit --no-fund
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOME=/home/node \
    XDG_CACHE_HOME=/home/node/.cache \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app ./
RUN mkdir -p /app/apps/web/.next/cache /home/node/.cache /tmp/motionly \
    && chown -R node:node /home/node /app/apps/web/.next /tmp/motionly
USER node
EXPOSE 3000
# Start Next directly: the production network has no egress, and a package
# manager shim (Corepack) would try to download pnpm at boot.
WORKDIR /app/apps/web
CMD ["node", "node_modules/next/dist/bin/next", "start"]
