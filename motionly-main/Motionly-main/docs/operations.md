# Production operations

Motionly exports Prometheus text at `GET /metrics`. Production deployments must set
`METRICS_BEARER_TOKEN`; scrape with `Authorization: Bearer <token>`. Every API response carries a
W3C `traceparent`, queue jobs retain the trace ID, and worker logs include it. Set
`OTEL_EXPORTER_OTLP_ENDPOINT` in the collector environment when logs are shipped to an OTLP-capable
backend.

The Phase 8 dashboard must graph these signals:

| Panel | Query / source | Alert |
| --- | --- | --- |
| API p95 latency and errors | `motionly_http_request_duration_ms_*` grouped by route/status | p95 > 2s or 5xx > 2% for 10m |
| Queue age and depth | `/metrics` queue snapshot gauges grouped by queue | oldest > 120s or dead-letter > 0 |
| Provider latency/error SLO | `motionly_provider_latency_ms_*` and `provider_slo_events` | success < 99% or p95 above provider budget |
| Worker freshness | `worker_heartbeats.last_seen_at` | any required queue has no heartbeat for 90s |
| Approved-export economics | `cost_attribution_events` grouped by category/export | cost per approved export exceeds plan margin |

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
and rotation expectations are documented in `infra/secrets/README.md`.
