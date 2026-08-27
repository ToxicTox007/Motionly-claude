# syntax=docker/dockerfile:1.7
# The wrapper is compiled against the exact Temporal server API version below;
# the resulting GHCR image is built, scanned, signed, and digest-pinned by the
# supply-chain workflow before it may enter a release manifest.
FROM golang:1.24.11-bookworm@sha256:656be510c8b4d33acf4eac8575b7f04a3e30b705c152dc6bde112dbe1a87b602 AS build
WORKDIR /src
COPY infra/temporal/server/go.mod ./
RUN go mod download
COPY infra/temporal/server/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/motionly-temporal-server ./cmd/motionly-temporal-server

FROM temporalio/server:1.27.1@sha256:704e5c5b44ad48ef16b24b67eb5552c4fc8e012d4f4c6d6bd8f7021b78137ecc
COPY --from=build /out/motionly-temporal-server /usr/local/bin/motionly-temporal-server
ENTRYPOINT ["/usr/local/bin/motionly-temporal-server"]
CMD ["start"]
