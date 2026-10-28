# Performance load tests

These scripts provide the repeatable acceptance harness for the performance plan.
They are intentionally opt-in so normal CI does not need live API credentials,
Redis, or k6 installed.

## API workflow scenarios

Run the k6 suite against a deployed or local API:

```bash
API_BASE_URL=http://localhost:8000 \
API_BEARER_TOKEN=... \
ADMIN_API_TOKEN=... \
WORKSPACE_ID=... \
PROJECT_ID=... \
RENDER_ID=... \
k6 run scripts/perf/k6-api-workflows.js
```

Covered surfaces:

- projects list and project detail
- upload init and upload complete
- plan create
- render status
- metrics scrape
- admin queue overview

Use the k6 summary as before/after evidence for p95 latency and error rate.

## BullMQ queue load

Run the live queue load test with Redis available:

```bash
RUN_BULLMQ_LOAD_TEST=1 \
REDIS_URL=redis://localhost:6379 \
LOAD_TEST_QUEUES=render,visual_analysis,matte_extraction \
LOAD_TEST_JOBS_PER_QUEUE=750 \
node scripts/perf/bullmq-queue-load.mjs
```

The test seeds more than 500 jobs per queue, verifies paginated queue reads
return every seeded job, prints oldest queued age, and removes the seeded jobs
before exiting.

## Browser performance

The Playwright browser performance suite measures a production build: the
config runs `next build`, then `next start` on port 3001 behind a local edge
proxy (`apps/web/tests/e2e/support/edge-proxy.mjs`) that serves `/api` and
`/supabase` on the public origin `http://127.0.0.1:3000`, as the deployed edge
does. It needs the API (started by the config) and Supabase.

App surfaces are measured signed in, through the login form. Either give a
user, or let the suite create a throwaway confirmed user with the service-role
key (it is deleted afterwards):

```bash
# Provided user (set PERF_PROJECT_ID, or a "Performance check" project is
# created in its workspace for the editor surface):
RUN_BROWSER_PERF=1 \
PERF_USER_EMAIL=... PERF_USER_PASSWORD=... \
PERF_PROJECT_ID=... \
pnpm perf:browser

# Throwaway user:
RUN_BROWSER_PERF=1 \
SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_ANON_KEY=... \
SUPABASE_SERVICE_ROLE_KEY=... SUPABASE_JWT_SECRET=... \
pnpm perf:browser
```

`/admin/ops` needs a platform admin: it is measured only with a provided admin
user and `PERF_USER_IS_ADMIN=1`.

Covered surfaces:

- landing
- login
- dashboard
- projects
- project editor
- billing
- account
- admin ops

The suite records navigation timing, first contentful paint, transfer size,
encoded body size and the Core Web Vitals (LCP, CLS and INP, from
PerformanceObserver; INP after one click on the page heading) to
`test-results/browser-performance.json`. Use those JSON samples as before/after
evidence for browser responsiveness.

Budgets: DCL 2.5 s, load 5 s, FCP 2.5 s, LCP 2.5 s, CLS 0.1 and INP 200 ms
(override with `BROWSER_PERF_DCL_MS`, `BROWSER_PERF_LOAD_MS`,
`BROWSER_PERF_FCP_MS`, `BROWSER_PERF_LCP_MS`, `BROWSER_PERF_CLS` and
`BROWSER_PERF_INP_MS`). By default the run observes: violations are written to
each sample's `violations` and shown as test annotations. With
`PERF_ENFORCE=1` any violation fails the run. A redirect away from a surface
always fails it, unless `ALLOW_BROWSER_PERF_REDIRECTS=1`.

## Golden media benchmarks

Run the media benchmark suite when FFmpeg, renderer dependencies, and optional
ML/GPU runtimes are available:

```bash
RUN_MEDIA_BENCHMARKS=1 \
MEDIA_BENCHMARK_OUTPUT=test-results/media-benchmarks.json \
pnpm perf:media
```

Covered surfaces:

- visual golden analysis
- matte segmentation
- FFmpeg render and export
- Remotion render
- Remotion encoded export
- publish pipeline
- cold/warm timing, disk, memory, GPU, and cache metadata in the JSON output

The script writes dry-run scenario metadata by default. Live runs execute the
existing golden/correctness suites, record duration and resource samples, and
fail if any benchmark command fails.

## Versioned SLO gate and staged canary

`scripts/perf/slo-contract.json` is the source of truth for canary decisions. It
requires schema version, contract revision, a five-minute window, minimum sample
counts, HTTP/5xx and p95 measurements by surface, queue backpressure and age,
provider health, and (when provided) baseline regression evidence. Validate a
collected metrics document locally with:

```bash
node scripts/perf/assert-slo.mjs \
  --contract scripts/perf/slo-contract.json \
  --canary test-results/canary/metrics.json \
  --revision <git-sha>
```

Use `--mode observe` while the metrics producer is being integrated; observe mode
writes a decision but does not fail. The default `--mode enforce` is fail-closed:
a malformed, stale, or undersampled document blocks promotion. The protected
canary workflow promotes 5%, then 25%, then 100% only after each stage validates.
Set `CANARY_ROLLBACK_HOOK` to a reversible platform rollback command before
enabling enforcement; the workflow preserves forward-only migrations and stores
the promotion, metric, decision, and rollback artifacts under
a `test-results/canary` directory. After a successful protected run,
`scripts/production-candidate-evidence.mjs` verifies the preflight and all three
enforced decisions, then writes the sanitized acceptance artifact at
`test-results/production-candidate/acceptance.json` for review.

For live k6 evidence, use strict mode and retain its JSON summary:

```bash
K6_STRICT_SLO=1 K6_SUMMARY_OUTPUT=test-results/k6-summary.json \
k6 run scripts/perf/k6-api-workflows.js
```
