# Production operations

Motionly exports Prometheus text at `GET /metrics`. Production deployments must set
`METRICS_BEARER_TOKEN`; scrape with `Authorization: Bearer <token>`. Every API response carries a
W3C `traceparent`, queue jobs retain the trace ID, and worker logs include it. Set
`OTEL_EXPORTER_OTLP_ENDPOINT` in the collector environment when logs are shipped to an OTLP-capable
backend.

The Phase 8 dashboard must graph these signals:

| Panel                      | Query / source                                                | Alert                                        |
| -------------------------- | ------------------------------------------------------------- | -------------------------------------------- |
| API p95 latency and errors | `motionly_http_request_duration_ms_*` grouped by route/status | p95 > 2s or 5xx > 2% for 10m                 |
| Queue age and depth        | `/metrics` queue snapshot gauges grouped by queue             | oldest > 120s or dead-letter > 0             |
| Provider latency/error SLO | `motionly_provider_latency_ms_*` and `provider_slo_events`    | success < 99% or p95 above provider budget   |
| Worker freshness           | `worker_heartbeats.last_seen_at`                              | any required queue has no heartbeat for 90s  |
| Approved-export economics  | `cost_attribution_events` grouped by category/export          | cost per approved export exceeds plan margin |

`GET /health/live` is the process liveness probe. `GET /health/ready` is the traffic readiness
probe and returns HTTP 503 unless Redis, Supabase REST, Storage, FFmpeg, configured providers, and
a current worker heartbeat all respond. The GPU compose topology includes web, API, Redis,
Supabase Auth/REST/Storage/Postgres, all worker queue classes, Caddy ingress, persistent volumes,
and file-mounted application secrets.

Validate and start the topology with:

```sh
docker compose --env-file .env.production -f infra/docker-compose.gpu.yml config --quiet
docker compose --env-file .env.production -f infra/docker-compose.gpu.yml up -d
```

The deployment environment must define `APP_DOMAIN`, `PUBLIC_ORIGIN`, `POSTGRES_PASSWORD`,
`REDIS_PASSWORD`, Supabase JWT keys, and provider credentials. Secret-file names and rotation
expectations are documented in [Runtime secrets](security/secrets.md).

Motionly has one public origin. The ingress (`infra/Caddyfile.routes`) sends `/api/*` to the API and
`/supabase/*` to the Supabase gateway, each with the prefix removed, and every other path to the web
app. The web image is configured when it starts (`PUBLIC_ORIGIN`, `API_INTERNAL_URL`,
`SUPABASE_URL`, `SUPABASE_ANON_KEY`; see `apps/web/.env.example`), so one image serves every
environment. Browser-facing Storage links use `SUPABASE_PUBLIC_URL` (`<origin>/supabase`); the API
and workers call Supabase over the private network.

In front of the ingress, CloudFront serves the public HTTPS address. `infra/aws/edge-contract.json`
describes it: only `/_next/static/*` and `/icon.svg` are cached; every other request, including its
`Authorization` header, cookies and viewer `Host`, reaches the origin uncached; error responses are
never cached; and the origin accepts only CloudFront's address range plus a secret
`X-Motionly-Origin-Verify` header. The CloudFormation template and the CPU-stage CDN simulator both
follow that file. The ingress serves plain HTTP on port 80 only; `infra/Caddyfile.origin-guard`
answers 403 to any request without the current (or, during a rotation, previous) secret, and the
routes remove the header before proxying. `infra/caddy-entrypoint.sh` refuses to start the
ingress without a valid secret. See [Runtime secrets](security/secrets.md) for rotation.

Auth rate-limits each viewer (`GOTRUE_RATE_LIMIT_HEADER`). After the origin guard, the ingress
replaces any `X-Motionly-Viewer-IP` the request carried with the address from CloudFront's
`CloudFront-Viewer-Address`, without the port; the web server passes it on when it calls Auth for a
viewer. GoTrue logs "rate limiting is not applied" for a request without it, and the CPU-stage log
scan treats that line as an error.

## Sessions

Signed-in pages are the sections of `apps/web/app/(app)` (`lib/signed-in-routes.mjs`). On those pages
the web proxy sends signed-out viewers to `/login?next=<page>` and renews the session when its
access token has less than two minutes left, so server components never need to renew it. The login
page returns only to a path on this site (`lib/safe-redirect.mjs`). Browser calls to the API read
the browser session's current token for every request and, if the API answers 401, renew the
session and retry once (`lib/api-session.mjs`). The API logs the user named by the verified token.

## Consented editorial learning

Editorial learning is globally **default-off** (`EDITORIAL_LEARNING_ENABLED=false`)
and is not an analytics export. When it is enabled, a user must first record a
versioned policy for memory and/or training. Only allow-listed normalized edit
deltas captured after that consent can be frozen into a per-workspace,
per-project, per-user dataset. Raw captions, transcripts, URLs, prompts, and
operational analytics never become training examples.

Enable it only after the Supabase migrations are applied and service-role RPC
health has been checked. Start with `EDITORIAL_LEARNING_SHADOW_SAMPLE_RATE=0` and
do not raise it until the frozen dataset evaluation has passed. Set bounded
retention (`EDITORIAL_LEARNING_RETENTION_DAYS`, 1–365) and a minimum dataset size
(`EDITORIAL_LEARNING_MINIMUM_EXAMPLES`, 1–10,000). A promoted deployment is
scoped to exactly one user, workspace, and project. Promotion and rollback use
revision-fenced RPCs; revoke is immediate, disables eligibility, revokes frozen
datasets, and removes active deployment use.

Do not enable the global flag merely to test persistence. Use a non-production
workspace, verify the policy/dataset/deployment audit rows and rollback, then
perform the staged canary described in `docs/ops/performance-load-tests.md`.
