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

Run the Playwright browser performance suite with a seeded app session:

```bash
RUN_BROWSER_PERF=1 \
PERF_PROJECT_ID=... \
pnpm perf:browser
```

Covered surfaces:

- landing
- login
- dashboard
- projects
- project editor
- billing
- account
- admin ops

The suite records navigation timing, first contentful paint when available,
transfer size, and encoded body size to `test-results/browser-performance.json`.
Use those JSON samples as before/after evidence for browser responsiveness.

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
counts, HTTP/5xx and p95 measurements by surface, queue backpressure, provider
health, and (when provided) baseline regression evidence. Validate a collected
metrics document locally with:

```bash
node scripts/perf/assert-slo.mjs test-results/canary/metrics.json
```

Use `--observe` while the metrics producer is being integrated; observe mode
writes a decision but does not fail. `SLO_ENFORCE=1` is fail-closed: a malformed,
stale, or undersampled document blocks promotion. The canary workflow promotes
5%, then 25%, then 100% only after each stage validates. Set
`CANARY_ROLLBACK_HOOK` to a reversible platform rollback command before enabling
enforcement; the workflow preserves forward-only migrations and stores the
promotion, metric, decision, and rollback artifacts under `test-results/canary`.

For live k6 evidence, use strict mode and retain its JSON summary:

```bash
K6_STRICT_SLO=1 K6_SUMMARY_OUTPUT=test-results/k6-summary.json \
k6 run scripts/perf/k6-api-workflows.js
```
