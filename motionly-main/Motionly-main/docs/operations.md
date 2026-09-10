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

The deployment environment must define `APP_DOMAIN`, `API_DOMAIN`, `SUPABASE_DOMAIN`,
`POSTGRES_PASSWORD`, `REDIS_PASSWORD`, Supabase JWT keys, and provider credentials. Secret-file names
and rotation expectations are documented in [Runtime secrets](security/secrets.md).

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
