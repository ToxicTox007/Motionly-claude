FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY packages/shared ./packages/shared
COPY apps/web ./apps/web
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @motionly/shared build && pnpm --filter @motionly/web build

FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate
ENV NODE_ENV=production \
    PORT=3000 \
    HOME=/home/node \
    XDG_CACHE_HOME=/home/node/.cache \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app ./
RUN mkdir -p /app/apps/web/.next/cache /home/node/.cache /tmp/motionly \
    && chown -R node:node /app/apps/web/.next /home/node/.cache /tmp/motionly
USER node
EXPOSE 3000
CMD ["pnpm", "--filter", "@motionly/web", "start"]
