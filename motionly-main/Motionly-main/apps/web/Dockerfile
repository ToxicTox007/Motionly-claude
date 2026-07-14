FROM node:20-bookworm-slim AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY packages/shared ./packages/shared
COPY apps/web ./apps/web
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @motionly/shared build && pnpm --filter @motionly/web build

FROM node:20-bookworm-slim
WORKDIR /app
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
