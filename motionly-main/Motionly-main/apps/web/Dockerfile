FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY packages/shared ./packages/shared
COPY apps/web ./apps/web
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @motionly/shared build && pnpm --filter @motionly/web build

FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20
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
