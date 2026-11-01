# syntax=docker/dockerfile:1.7
# The wrapper is compiled against the exact Temporal server API version in
# go.mod (go.temporal.io/server); the resulting GHCR image is built, scanned,
# signed, and digest-pinned by the supply-chain workflow before it may enter a
# release manifest.
FROM golang:1.26.10-bookworm@sha256:23ce71337b726608afb1bc00986e630a05034a87a87ea1fc2cd1ae5cf33387eb AS build
WORKDIR /src
COPY infra/temporal/server/go.mod infra/temporal/server/go.sum ./
RUN go mod download
COPY infra/temporal/server/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/motionly-temporal-server ./cmd/motionly-temporal-server

# Runtime: the wrapper (the Temporal server is compiled into it, and its
# `health` command replaces the Temporal CLI health check) on a minimal Alpine
# with the busybox tools start-server.sh uses, CA certificates and time zone
# data. The upstream temporalio/server image's own server, tctl, temporal CLI
# and dockerize binaries were never run, so they are not shipped.
FROM alpine:3.24.3@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a
RUN apk add --no-cache ca-certificates tzdata \
    && addgroup -g 1000 temporal \
    && adduser -u 1000 -G temporal -D -H temporal
COPY --from=build /out/motionly-temporal-server /usr/local/bin/motionly-temporal-server
USER temporal
WORKDIR /etc/temporal
ENTRYPOINT ["/usr/local/bin/motionly-temporal-server"]
CMD ["start"]
