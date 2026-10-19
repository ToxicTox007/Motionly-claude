# Local development environment

The repository root is `motionly-main/Motionly-main`. The setup scripts create `infra/.env.local` and the local secret-file placeholders used by Docker Compose.

## Prerequisites

- Docker Desktop or Docker Engine with Compose
- Bash for `setup-local-env.sh`, or PowerShell for `setup-local-env.ps1`
- Node.js and pnpm for application commands
- OpenSSL when generating local certificates or running certificate-dependent checks

## Initialize local configuration

From the repository root, run one of:

```bash
bash setup-local-env.sh
```

```powershell
.\setup-local-env.ps1
```

The scripts create local-only files under `infra/`. Never commit `infra/.env.local`, populated secret files, certificates, or generated credentials.

For the secret-file contract, see [Runtime secrets](../security/secrets.md). For the production topology and health checks, see [Operations](../operations.md).

## Start the minimal stack

```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d postgres redis temporal-postgres
```

On PowerShell, use the same command on one line or use PowerShell's backtick line continuation.

## Start the full development stack

```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d --build
```

Inspect service status with:

```bash
docker compose --env-file infra/.env.local -f infra/docker-compose.gpu.yml ps
```

The GPU workers (`gpu-inference-worker`, `matte-worker`, `gpu-ffmpeg-worker`) start only with `--profile gpu` and need NVIDIA GPUs.

## Run the CPU-stage stack

The CPU-stage stack is the production topology without GPU hardware: GPU-dependent capabilities run as labelled test fixtures (`ML_EXECUTION_MODE=test_fixture`) and external AI and media providers use their test providers. It builds images from your checkout, generates throwaway secrets outside the repository, starts from empty volumes, checks it, and removes everything afterwards:

```bash
pnpm verify --stage stack                    # slim: one CPU worker, fits 2 CPUs / 7 GB
CPU_STAGE_SIZE=full pnpm verify --stage stack  # every production CPU worker service
```

Results, including per-service peak memory and CPU, are written to `test-results/cpu-stage/`. The overlay is `infra/docker-compose.cpu-stage.yml`; health output reports `"gpu_stage": "not_enabled"`.

The stack has one public origin, `https://127.0.0.1:18443`, served by the CloudFront simulator (`scripts/cdn-sim`, test infrastructure, not CloudFront) that applies `infra/aws/edge-contract.json`; `http://127.0.0.1:18081` redirects to it. Its certificate comes from a per-run test CA: `edge_ca_cert` in the generated secrets directory (`secrets_root` in `test-results/cpu-stage/state.json`). Behind it, the ingress routes `/api/*` to the API, `/supabase/*` to the Supabase gateway and every other path to the web app (`infra/Caddyfile.routes`). The ingress is also bound to `http://127.0.0.1:18080`, where it refuses every request that lacks the origin secret, as the production origin does. All ports are loopback only.

GPU-dependent capabilities and external providers run as labelled stand-ins in the CPU stage. Every stand-in is listed in [`fixtures/registry.json`](../../fixtures/registry.json) with its label, how to recognise its output, the setting that turns it on, and how production refuses it. `pnpm verify --stage leakage` fails when a stand-in is not registered, production accepts a setting that turns one on, a production deployment input enables one, or test infrastructure (the CloudFront simulator, the per-run test CA) is wired into production. A fixture proves the workflow and integration contract, not the quality or availability of the capability it stands in for.

## Run the browser suite

The browser suite (`apps/web/tests/browser`, config `apps/web/playwright.browser.config.mjs`) runs against the CPU-stage stack through the CloudFront simulator, in Chromium, Firefox, WebKit, a Pixel 7 and an iPhone 14. Every test fails on a console error, an uncaught page error, a failed request, any 5xx response, or a serious or critical WCAG 2.1 A/AA issue found by axe. It needs the Playwright browsers and `ffmpeg` (the upload journey generates a short test video):

```bash
pnpm --filter @motionly/web exec playwright install --with-deps chromium firefox webkit
pnpm verify --stage browser   # build, start from empty volumes, suite, report, log scan, verdict, tear down
```

The stage writes `test-results/browser/` (JUnit, JSON results, `report.json` with per-setup results and navigation timings, and traces of failures) and `test-results/cpu-stage/logscan.json` and `resources.json`. The log scan fails on any error line a service logged during the suite, except registered known defects in `tests/cpu-stage/known-log-defects.json`, each with the task that fixes it. A test that is expected to fail (`test.fail`) must name that task too.

Every test records the time to first byte, DOMContentLoaded and load of each full page load; `report.json` summarises them per setup and per page. The CPU-stage budgets in `tests/cpu-stage/resource-budget.json` were approved at Decision D2: each service's memory and CPU budget, the p95 page-load time per setup, and the suite's total duration are 1.25× the measured baselines (`python scripts/verify/record_baselines.py` records baselines; `--approve` sets budgets). Firefox's page-load timings are excluded from the budgets: on the CPU stage its Navigation Timing values are implausible (time to first byte p50 0 ms, load p50 24 ms, against 330–500 ms in every other setup). Firefox still runs every browser test and quality gate.

To iterate against a stack that is already running (`python scripts/verify/cpu_stage_stack.py up`):

```bash
cd apps/web
pnpm test:browser --project=firefox tests/browser/auth.spec.mjs
```

The CPU stage issues 150-second access tokens (`GOTRUE_JWT_EXP` in `infra/docker-compose.cpu-stage.yml`) so that `tests/browser/session.spec.mjs` sees a token expire and renew within one test. Tokens must outlive the web proxy's two-minute renewal margin; shorter ones would be renewed by server components, which cannot save the new refresh token. Because the whole suite is one viewer behind the CDN simulator, the overlay also raises Auth's per-viewer token limit (`GOTRUE_RATE_LIMIT_TOKEN_REFRESH`). Production keeps GoTrue's defaults.

## Run the web app against a local API

The web app is configured when it starts, not when it is built: copy `apps/web/.env.example` to `apps/web/.env.local` and set `PUBLIC_ORIGIN`, `API_INTERNAL_URL`, `SUPABASE_URL`, and `SUPABASE_ANON_KEY`. Under `next dev` the web server stands in for the ingress and proxies `/api` and `/supabase`, so the browser uses one origin, as in production. Point the API at the same origin (`API_PUBLIC_BASE_URL=<origin>/api`, `SUPABASE_PUBLIC_URL=<origin>/supabase`) so the links it hands out work in the browser. `next start` does not proxy these paths; run it behind the ingress.

## Troubleshooting

- If Compose reports a missing variable, rerun the setup script and inspect `infra/.env.local`.
- If a secret file is missing, verify `SECRETS_DIR` and the file names in [Runtime secrets](../security/secrets.md).
- If a service cannot connect, start PostgreSQL, Redis, and Temporal PostgreSQL before application services.
- If Temporal certificate setup is required, follow [Temporal mTLS canary](../ops/temporal-mtls-canary.md) and use only local, ignored certificate files.
