# Motionly Production Hardening & Antigravity Execution Plan

**Purpose:** Convert the Motionly source-code audit, AI/intelligence audit, and three model-generated remediation drafts into one executable, verifiable, anti-hallucination remediation plan suitable for Antigravity, Claude Code, Cursor, or a human engineering team.

**Source inputs merged:**

- `source-audit.md` — overall source-code, architecture, security, ops, schema, deployment, and readiness audit.
- `AI_INTELLIGENCE_AUDIT.md` — AI/ML capability, provider, heuristic, pipeline, cost, and AI-quality audit.
- GLM Turbo remediation plan — useful for direct engineering checklists, Runway cost guards, and transcription fallback.
- DeepSeek remediation plan — useful for truthfulness, concrete GPU crash guards, MediaPipe Selfie Segmentation stopgap, evals, and measurable go/no-go gates.
- Claude Sonnet readiness plan — used as the execution-safety wrapper: verify-first rules, scope locks, decision gates, proof requirements, and final readiness gate.

**Core principle:** do not choose between the prior plans. Merge them. Claude's plan supplies the safest execution method; GLM and DeepSeek supply additional concrete implementation specifics.

---

## 0. Mandatory Operating Rules for Antigravity / Coding Agent

These rules apply before and during every phase. They are not optional.

### 0.1 Verify before modifying

The audits and remediation plans are evidence, not ground truth. Before changing any code:

1. Open the actual file.
2. Confirm the function, variable, endpoint, or behavior still exists.
3. Confirm the audit claim is still true.
4. Record the verified file path and line numbers in `REMEDIATION_LOG.md`.

If the file path, function name, line number, or behavior no longer matches, stop and report the discrepancy. Do not silently invent a replacement fix.

### 0.2 No invented infrastructure

Do not add Kubernetes, HPA, Helm, Terraform, cloud-specific autoscaling, new services, new queues, or new providers unless the repository already contains evidence of that infrastructure or the human operator explicitly approves it.

Known audit-supported infrastructure includes Docker, Docker Compose, Caddy, Redis/BullMQ, Postgres/Supabase, worker images, FFmpeg/Remotion, and GitHub Actions. Kubernetes autoscaling is **not** confirmed by the audits and must be treated as **DECISION NEEDED**.

### 0.3 No silent product decisions

The following are decision gates, not engineering assumptions:

- Which migration tree is canonical.
- Whether to build real background segmentation immediately or relabel/disable the current feature.
- Whether production infra uses Docker Compose, Kubernetes, managed queues, or another substrate.
- What VRAM budget exists per GPU worker.
- Which paid AI providers are allowed as fallbacks.
- What monthly cost caps and per-workspace quotas should be.
- What retention period is acceptable for raw uploads, transcripts, visual manifests, and generated exports.

When one of these appears, pause and ask for confirmation.

### 0.4 One task, one change, one proof

Each task must produce:

- files changed,
- exact change summary,
- verification command or manual check,
- observed result,
- linked audit finding addressed.

Do not mark anything done without proof.

### 0.5 Stay inside scope

Every phase lists an intended scope. Do not expand beyond that scope unless necessary. If necessary, first write why the additional file or subsystem must be touched.

### 0.6 Preserve behavior unless the task explicitly changes it

No drive-by refactors. No dependency upgrades unless required by the task. No renaming public APIs unless the task requires it and frontend/client callers are updated in the same task.

### 0.7 Maintain `REMEDIATION_LOG.md`

Create or update `REMEDIATION_LOG.md` at the repo root. After each task, append:

```md
## Phase X / Task X.Y — <title>

- Date:
- Executor:
- Files touched:
- Audit finding resolved:
- What changed:
- Verification performed:
- Verification output / evidence:
- Remaining caveats:
```

### 0.8 Security-sensitive phases require human review

Phases touching auth, migrations, secret handling, provider data sharing, billing, retention, or production configuration require human review even if tests pass.

### 0.9 Do not declare production-ready early

Motionly may only be described as production-ready after the final readiness gate in Phase 13 passes with evidence.

---

## 1. Phase Map

| Phase | Name | Priority | Main Risk Addressed |
|---|---:|---:|---|
| 1 | Auth bypass kill switch | P0 | Production auth bypass / workspace compromise |
| 2 | Async planner fast path | P0 | FastAPI thread blocking on OpenRouter |
| 3 | Render upload streaming | P1 | Worker OOM on large exports |
| 4 | Background-removal truthfulness | P0 quality | Fake AI feature / user trust risk |
| 5 | Migration consolidation | P1 | Schema drift / release-order risk |
| 6 | Worker-owned heavy jobs | P1 | Mixed API/local/worker execution paths |
| 7 | AI pipeline error isolation | P1 | Provider/ML failures crashing pipeline |
| 8 | GPU OOM controls and model isolation | P1 | Worker crashes / sequential bottlenecks |
| 9 | Planner correctness, fallback transparency, cache invalidation | P1/P2 | Silent prompt discard / stale plans |
| 10 | Cost controls and provider quotas | P1 | Runway/OpenRouter surprise bills |
| 11 | Observability, live contract tests, evals | P1/P2 | Provider drift and AI quality regressions unseen |
| 12 | Security, privacy, retention, secret logging | P1 | Data leakage / log leaks / indefinite retention |
| 13 | Final production readiness gate | Gate | Evidence-based launch decision |
| 14 | Post-launch AI enhancements | Backlog | Non-blocking value features |

---

# Phase 1 — Kill Auth Bypass Flags

**Priority:** P0  
**Goal:** Production must not start if any auth bypass or mock identity path is enabled.

## Why

The source audit identifies auth bypass flags such as `SKIP_AUTH_MIDDLEWARE` and `SKIP_API_AUTH`, plus local mock/header-driven identity behavior, as the highest-severity security risk. A misconfigured deployment could bypass real authentication and compromise workspace isolation.

## Scope

Verify exact files before editing. Expected scope:

- `apps/web/proxy.ts`
- `apps/web/lib/api-auth.ts`
- `apps/api/app/services/auth.py`
- `apps/api/app/core/config.py`
- production Dockerfiles / Compose / deploy manifests only as needed
- auth tests and CI checks

## Tasks

### 1.1 Locate all bypass and mock identity flags

Search for:

- `SKIP_AUTH_MIDDLEWARE`
- `SKIP_API_AUTH`
- `MOCK_AUTH`
- `MOCK_AUTH_ENABLED`
- `LOCAL_AUTH`
- `BYPASS_AUTH`
- header-driven actor/workspace identity flags
- fixture/mock provider flags if they can affect production auth or identity

Record all read sites and default assignments.

### 1.2 Determine actual production environment variable

Do not assume `ENVIRONMENT`, `APP_ENV`, or `NODE_ENV` without checking current code.

Find the actual production-mode detection used in API and web code. If multiple exist, document them and propose one canonical production guard.

### 1.3 Add hard startup failure

Add production assertions so that:

- API process crashes on startup if production mode and any bypass/mock identity flag is truthy.
- Web app/proxy/build/runtime crashes or fails loudly if production mode and bypass middleware is truthy.
- The failure is an exception/process exit, not a warning.

### 1.4 Remove truthy production defaults

Search deploy-related files:

- Dockerfiles
- Compose files
- GitHub Actions workflows
- deployment scripts
- `.env.production` examples, if any

No production-targeted file should set bypass/mock auth flags to truthy values.

### 1.5 Add negative tests

Add tests proving:

- production mode + bypass flag => boot/config validation fails;
- production mode + normal auth config => boot/config validation passes.

### 1.6 Add CI guard

Add a simple CI check that fails if production manifests contain truthy assignments for known bypass flags.

## Verify

- Negative auth-bypass startup test passes.
- Normal startup config test passes.
- Grep of production manifests shows no truthy bypass assignments.
- CI runs the guard.

## Stop / escalate

If additional bypass behavior exists outside the listed flags, stop and ask whether to include it in Phase 1 or create a separate security issue.

---

# Phase 2 — Move Edit Planner to Async Queue First

**Priority:** P0  
**Goal:** `POST /plans` must return quickly and never block FastAPI threads on OpenRouter latency.

## Why

The AI audit flags synchronous OpenRouter edit-planner calls as a P0 risk. LLM latency can block request threads for 20–45 seconds, exhausting connection pools and causing user-facing timeouts.

This phase intentionally handles the edit planner before broader queue cleanup because it is the most urgent blocking-path risk.

## Scope

Expected files:

- `apps/api/app/api/plans.py`
- `apps/api/app/services/edit_planner.py`
- `apps/api/app/services/queue.py`
- `apps/worker/src/worker.mjs`
- `apps/worker/src/queue-common.mjs`
- `apps/worker/src/queue-bridge.mjs`
- frontend call sites that assume synchronous plan response:
  - `apps/web/components/editor-client.tsx`
  - `apps/web/lib/client-api.ts`

## Tasks

### 2.1 Verify existing PlanQueue shape

Open queue definitions and worker dispatch. Confirm:

- whether `PlanQueue` exists;
- its queue name;
- expected job payload;
- whether worker already has a plan job handler;
- where plan results are persisted.

Do not invent a new queue if a compatible queue already exists.

### 2.2 Convert `POST /plans` to enqueue

Change the plan creation endpoint so it:

1. validates request/auth/workspace normally;
2. creates or references a plan-generation job record;
3. enqueues work on `PlanQueue`;
4. returns `202 Accepted` with a job ID and status URL or poll identifier;
5. does not call OpenRouter in the request thread.

### 2.3 Add or reuse status path

Before adding a new endpoint, search for existing job-status/render-status/polling patterns. Reuse the existing status convention if present.

The status response should expose:

- queued/running/succeeded/failed;
- progress if available;
- resulting plan ID when complete;
- failure reason if failed.

### 2.4 Update frontend flow

Update UI/client code so plan generation becomes:

submit → show pending state → poll/subscribe → load plan result.

Do not break existing editor state handling.

### 2.5 Add tests

Tests should prove:

- endpoint returns `202` quickly;
- OpenRouter provider is not called during the HTTP request;
- queued worker path eventually persists the plan result;
- failed worker job produces a visible failed status.

## Verify

- `POST /plans` responds in under 500ms in a local/staging test even with a mocked 20–45s provider delay.
- Concurrent plan requests do not block API worker/thread pool.
- Frontend receives and displays final plan after async completion.

---

# Phase 3 — Stream Render Uploads

**Priority:** P1  
**Goal:** Large Remotion exports must upload without loading the whole file into memory.

## Why

The source audit flags the premium Remotion worker path for reading rendered files fully into memory before upload. That creates OOM risk for large video exports and can trigger worker crashes and retry storms.

## Scope

Expected file:

- `apps/worker/src/remotion.mjs`

Potential supporting files:

- storage helper modules used by worker
- render tests

## Tasks

### 3.1 Confirm buffer-based upload

Locate the exact code path that uses `fs.readFileSync`, `readFile`, Buffer loading, or equivalent full-file memory buffering before upload.

Record line numbers in `REMEDIATION_LOG.md`.

### 3.2 Replace with streamed upload

Use `fs.createReadStream` or the repository's existing streaming/signed upload mechanism.

Do not assume Supabase JS accepts streams in the current runtime until verified. If Supabase client does not support the intended stream shape, use the existing signed upload URL flow or a compatible multipart/chunked upload pattern.

### 3.3 Handle partial failures

On mid-stream failure:

- mark render job failed or retryable according to existing job semantics;
- clean up partial object if storage layer exposes a delete path;
- retry from scratch unless resumable upload support is already confirmed.

### 3.4 Add memory-bound test

Create a large synthetic file test that proves memory use stays bounded and does not scale linearly with file size.

A good target:

- local synthetic file large enough to fail the old approach under a constrained memory limit;
- new implementation remains under the configured memory ceiling.

## Verify

- Existing golden render/upload tests still pass.
- Synthetic large upload test passes.
- Staging test uploads a real large export while worker memory stays below the defined threshold.

---

# Phase 4 — Background Removal Truthfulness

**Priority:** P0 quality / trust  
**Goal:** Motionly must not expose a fake AI background-removal feature as if it were real segmentation.

## Why

The AI audit says background removal is currently a static CSS ellipse in Remotion or periodic FFmpeg box blur. There is no ML-based background segmentation. If described as AI subject isolation, this is a user-trust defect.

## Scope

Expected files:

- `apps/renderer/src/components/VideoScene.tsx`
- `apps/api/app/services/ffmpeg_render.py`
- UI copy containing “background removal,” “AI background,” “cutout speaker,” etc.
- If building a stopgap: relevant visual/renderer pipeline files

## Decision gate

**DECISION NEEDED:** choose one production-facing option.

### Option A — Real segmentation now

Integrate a real segmentation model into the GPU/visual pipeline and render from alpha masks.

Candidate models:

- BiRefNet
- RMBG-1.4
- MediaPipe Selfie Segmentation as a lightweight interim option

MediaPipe Selfie Segmentation is acceptable as a stopgap only if the product copy accurately describes it as lightweight/experimental and quality limitations are tested.

### Option B — Honest relabel / disable now, build later

Hide the feature, disable the toggle, or relabel it accurately as:

- “center oval cutout,”
- “experimental blur/crop effect,”
- “background removal coming soon.”

Until Option A is approved and implemented, production-facing UI must default to Option B.

## Tasks for Option B

### 4.1 Find all misleading copy

Search UI, API docs, comments visible to users, admin UI, and settings labels for:

- AI background removal
- background removal
- cutout speaker
- portrait cutout
- subject isolation
- remove background

### 4.2 Relabel or hide

Change product-facing labels so they match actual behavior.

### 4.3 Add regression test

Add a test that prevents future copy from claiming ML/AI segmentation when implementation is ellipse/blur.

## Tasks for Option A

### 4.4 Verify current render data flow

Before adding a model, confirm how visual manifest and render props flow into Remotion and FFmpeg.

### 4.5 Implement segmentation behind resource controls

If using BiRefNet/RMBG/MediaPipe:

- load model in GPU/vision worker or CPU path as appropriate;
- respect Phase 8 concurrency/OOM controls;
- output per-frame alpha masks or a compact mask representation;
- store masks in a way compatible with render jobs;
- update Remotion and FFmpeg to use masks, not ellipse/blur.

### 4.6 Add visual golden test

Test an off-center subject. The output should segment subject shape rather than crop a fixed center oval.

## Verify

- Production UI does not claim fake AI segmentation.
- If Option A is implemented, visual golden test proves off-center subject is preserved.
- If Option B is chosen, product copy accurately states the limitation.

---

# Phase 5 — Consolidate Migration Trees

**Priority:** P1  
**Goal:** One canonical migration source, fresh bootstrap works, schema drift eliminated.

## Why

The source audit identifies both `supabase/migrations` and `infra/migrations`, plus enum/status repair history. It says the exact canonical migration source is not provable from code alone.

## Scope

Expected files/directories:

- `supabase/migrations/`
- `infra/migrations/`
- `supabase/config.toml`
- `scripts/check_forward_migrations.sh`
- `supabase/tests/phase_0_schema_test.sql`
- `supabase/tests/phase_9_rls_test.sql`
- schema enum definitions in shared/API packages as needed

## Decision gate

**DECISION NEEDED:** choose canonical migration source.

Recommendation may be `supabase/migrations` because Supabase tooling often expects it, but do not choose this silently. Confirm what the deployment pipeline actually runs today.

## Tasks

### 5.1 Diff migration trees

List:

- migrations only in `supabase/migrations`;
- migrations only in `infra/migrations`;
- overlapping migrations with different content;
- enum/status repair migrations;
- any order conflicts.

### 5.2 Port missing migrations

After canonical source is confirmed, port any missing migration content into the canonical tree in forward-safe order.

Do not rewrite already-applied production migration history unless the team explicitly approves a reset strategy.

### 5.3 Archive or mirror non-canonical tree

Do not leave two hand-maintained migration sources.

Either:

- move non-canonical tree to `legacy/` with README warning, or
- make it a generated mirror with no manual edits.

### 5.4 Update scripts and CI

Update all scripts and workflows to use only the canonical source.

### 5.5 Normalize legacy enums/statuses

Investigate legacy enum/style values referenced in audits. Confirm which are still emitted or read. Add data backfill migrations only after confirming production data requirements.

### 5.6 Run schema/RLS tests

Fresh database bootstrapped from canonical migrations must pass schema and RLS tests.

## Verify

- Fresh bootstrap from canonical migrations only succeeds.
- Schema tests pass.
- RLS tests pass.
- CI fails if non-canonical migration tree is edited as source of truth.

---

# Phase 6 — Worker-Owned Heavy Jobs and Production Fallback Removal

**Priority:** P1  
**Goal:** In production, API is control plane only; workers execute heavy jobs.

## Why

The source audit flags mixed execution modes: local in-process fallbacks, internal API job execution, worker-only paths, and direct worker writes. This makes production behavior hard to reason about and can hide bugs.

## Scope

Expected files:

- `apps/api/app/api/internal_jobs.py`
- `apps/api/app/services/queue.py`
- route files for visual analysis, music, effects, regeneration, rendering, b-roll, transcription, and scale jobs
- worker dispatch files under `apps/worker/src/`
- frontend clients if API behavior changes

## Tasks

### 6.1 Inventory heavy operations

List all endpoints that can trigger:

- transcription
- visual analysis
- edit planning
- b-roll generation/retrieval
- music recommendation/selection jobs
- effects generation
- regeneration
- rendering/export
- publishing
- scale jobs: Runway, Demucs, style analysis, preference learning

For each, record whether it runs inline, enqueues, or has local fallback.

### 6.2 Gate local fallback paths as non-production only

Any in-process fallback must be impossible in production.

Use the same hard-fail pattern as Phase 1: production mode + local fallback enabled => startup failure.

### 6.3 Convert remaining sync heavy endpoints

For endpoints that still run heavy work inline:

- enqueue job;
- return `202`;
- expose status/result path;
- update frontend call sites.

### 6.4 Validate worker mutation channels

If workers write directly to Supabase with service-role credentials:

- document why;
- verify they apply the same validation/invariants as the API;
- ensure worker endpoints are not publicly reachable.

### 6.5 Add tests

Tests should prove:

- production cannot enable in-process fallback;
- heavy endpoints enqueue rather than execute inline;
- missing worker/Redis returns explicit failure, not silent local execution.

## Verify

- API returns 202 for heavy operations.
- Production fallback flag hard-fails startup.
- Queue/worker smoke test succeeds end to end.

---

# Phase 7 — AI Pipeline Error Isolation and Transcription Fallback

**Priority:** P1  
**Goal:** Provider or ML failures degrade gracefully and do not crash unrelated pipeline stages.

## Why

The audits flag provider fragility: Groq transcription has a fixed timeout and no retry; audio ML providers may be partial; visual context may be unavailable; LLM outputs can fail schema validation. The system needs explicit failure isolation.

## Scope

Expected files:

- `apps/api/app/services/transcription_provider.py`
- `apps/api/app/services/transcription_jobs.py`
- `apps/api/app/services/edit_planner.py`
- `apps/api/app/services/visual_intelligence.py`
- `apps/api/app/services/audio_ml_providers.py`
- Runway/scale provider code if in scope for paid generation errors

## Tasks

### 7.1 Map all external and ML calls

Create a table in `REMEDIATION_LOG.md`:

| Provider/model | File | Call site | Current timeout | Retry? | Failure behavior |
|---|---|---|---:|---|---|

Include:

- Groq Whisper
- OpenRouter
- Runway Gen-3
- YOLOv8
- MediaPipe
- BLIP-2
- CLIP
- Pyannote
- Wav2Vec2
- YAMNet
- Demucs if applicable

### 7.2 Wrap unguarded calls

Failures should produce well-defined degraded outputs, not crash the entire pipeline.

Examples:

- audio ML fails => transcript words still saved;
- visual analysis fails => planner receives `visual_context_unavailable`;
- OpenRouter invalid output => validator/repair path signals fallback used;
- Runway fails => b-roll generation job fails clearly, no duplicate cost retry loop.

### 7.3 Add transcription retry and fallback

For Groq Whisper:

- add bounded retry with exponential backoff for transient errors;
- keep total timeout bounded;
- add fallback provider only after human confirms allowed providers and cost/privacy implications.

Potential fallback chain from GLM: Groq → Deepgram → AssemblyAI. Treat this as **DECISION NEEDED**, not automatic.

### 7.4 Resolve partial audio ML states

For Pyannote/Wav2Vec2/YAMNet:

- either fully package dependencies/tokens and expose health/status;
- or mark as disabled/unavailable and fall back gracefully to heuristics;
- do not pretend partial providers are live.

### 7.5 Add failure-injection tests

For each external/ML provider:

- timeout;
- invalid response;
- provider 5xx;
- missing token/dependency;
- malformed model output.

Assert downstream consumers receive well-formed degraded state.

## Verify

- Failure-injection tests pass.
- Successful-path tests still pass.
- Pipeline saves partial outputs where appropriate.

---

# Phase 8 — GPU Worker Resource Controls and Model Isolation

**Priority:** P1  
**Goal:** GPU visual analysis cannot crash the worker fleet under load or OOM.

## Why

The AI audit says YOLOv8, MediaPipe, BLIP-2, and CLIP may load in a single worker process. BLIP-2 can require significant VRAM, so simultaneous jobs risk CUDA OOM.

## Scope

Expected files:

- `apps/api/app/services/visual_intelligence.py`
- `apps/worker/src/worker.mjs`
- `apps/worker/Dockerfile.gpu`
- `infra/docker-compose.gpu.yml`
- queue concurrency config

## Tasks

### 8.1 Verify current concurrency

Read worker dispatch. Determine whether GPU jobs run:

- strictly one at a time;
- per queue concurrency > 1;
- across multiple processes;
- with any GPU semaphore already present.

### 8.2 Add explicit concurrency cap

DeepSeek's concrete recommendation is a semaphore of 1 concurrent GPU inference per worker. Use that as the safe default unless target VRAM and profiling justify a higher number.

Do not invent target hardware. Record **DECISION NEEDED** for actual VRAM budget.

### 8.3 Add CUDA OOM handling

Catch CUDA OOM exceptions and:

- mark only the specific job failed or degraded;
- release memory if possible;
- keep worker daemon alive;
- avoid losing unrelated in-flight jobs.

### 8.4 Consider model isolation

DeepSeek/GLM suggest isolating BLIP-2 with an 8GB limit or splitting vision/embedding workers. Treat exact numbers as target recommendations, not facts.

**DECISION NEEDED:** choose one:

- single process + concurrency cap + lazy loading;
- separate worker processes for YOLO/MediaPipe vs BLIP-2/CLIP;
- separate containers with explicit memory/GPU limits.

### 8.5 Avoid unverified autoscaling assumptions

Do not implement Kubernetes HPA unless repo/deployment evidence confirms Kubernetes is target infra.

Alternative if Docker Compose is current infra:

- expose queue-depth metrics;
- document manual worker replica scaling;
- add alerts when queue depth exceeds threshold;
- only later migrate to HPA if platform decision confirms Kubernetes.

### 8.6 Add load tests

Simulate more visual-analysis jobs than concurrency cap. Assert jobs queue rather than crash.

## Verify

- Concurrency cap works.
- Injected CUDA OOM fails one job, not worker process.
- Queue backlog behavior is observable.

---

# Phase 9 — Edit Planner Correctness, Fallback Transparency, and Cache Invalidation

**Priority:** P1/P2  
**Goal:** Planner must not silently discard prompts or serve stale visual-context plans.

## Why

The AI audit says LLM validation/repair can replace custom plans with deterministic fallback templates. It also warns stale plans may be served if the transcript ID remains stable while visual manifest changes.

## Scope

Expected file:

- `apps/api/app/services/edit_planner.py`

Potential supporting files:

- plan schemas
- edit plan persistence code
- frontend display of plan-generation warnings

## Tasks

### 9.1 Inspect validation/repair behavior

Open:

- `_validate_or_repair_plan`
- `_repair_plan_payload`
- fallback template generation
- cache key builder, likely `build_plan_cache_key`

Record exactly when full fallback occurs vs field-level repair.

### 9.2 Surface fallback usage

If the system uses deterministic fallback because LLM output is invalid:

- persist `fallback_used: true` or equivalent;
- log structured reason;
- expose warning to API/frontend if user prompt may have been discarded.

### 9.3 Fix cache key

Cache key should include stable hashes/versions for:

- transcript content or transcript ID + updated marker;
- visual manifest content/version/hash;
- prompt text;
- prompt version;
- template/style settings;
- user feedback/regeneration instructions;
- relevant model/provider version.

### 9.4 Add tests

Tests:

- same transcript ID + changed visual manifest => cache miss;
- malformed LLM response => fallback signal present;
- repaired-but-not-fallback response preserves user-specific intent where possible.

## Verify

- Cache invalidation tests pass.
- Fallback transparency test passes.
- Existing plan tests still pass.

---

# Phase 10 — Cost Controls and Provider Quotas

**Priority:** P1  
**Goal:** Paid external AI calls must be quota-checked before invocation.

## Why

The AI audit flags uncapped Runway Gen-3 generation as a cost risk. OpenRouter and future providers can also incur cost. Cost controls should not wait until late privacy/ops phases.

## Scope

Expected files:

- `apps/api/app/services/scale.py`
- Runway provider code
- OpenRouter planner provider path
- billing/cost attribution services
- workspace quota or billing status code

## Decision gate

**DECISION NEEDED:** define quota policy.

Examples:

- per-workspace monthly Runway seconds;
- per-user daily generation attempts;
- per-project retry cap;
- plan-tier-based OpenRouter token/call caps;
- admin override behavior.

## Tasks

### 10.1 Inventory paid calls

List paid/externally metered calls:

- Runway Gen-3
- OpenRouter
- Groq if billed
- Deepgram/AssemblyAI if added later
- stock providers if metered
- future image/dubbing providers

### 10.2 Add pre-call quota guard

Before each paid call:

- check workspace/user/project quota;
- reserve estimated usage if appropriate;
- reject with clear error if over quota;
- avoid making provider call when rejected.

### 10.3 Record actual usage

After successful call:

- record provider, model, workspace, project, units, estimated cost, external task ID if any;
- handle failed/canceled provider tasks carefully.

### 10.4 Add tests

Tests:

- over-quota Runway request does not invoke provider;
- under-quota request invokes provider and records usage;
- repeated retry attempts hit cap.

## Verify

- Provider mock confirms no call on over-quota.
- Usage records created on successful call.
- Admin/ops visibility exists or follow-up is logged.

---

# Phase 11 — Observability, Live Provider Contract Tests, and AI Evals

**Priority:** P1/P2  
**Goal:** Detect provider drift, quality regressions, queue backlogs, and cost/SLO failures before users do.

## Why

The audits say provider contract tests are fixture-backed, live integration checks are missing, SLO tracking relies on manual latency reports, and no LLM/visual quality evals exist.

## Scope

Expected files/directories:

- `apps/api/tests/`
- `apps/api/tests/test_provider_contracts.py`
- `.github/workflows/`
- analytics/provider SLO/worker heartbeat code
- admin ops UI if surfacing metrics

## Tasks

### 11.1 Add gated live provider contract tests

Create secret-gated workflow, not run on every commit by default.

Providers:

- Groq
- OpenRouter
- Runway
- Pexels/Pixabay if used in production

Test minimal request/response schema and latency bounds.

### 11.2 Require consecutive clean runs before GA

DeepSeek's measurable gate is useful: require 3 consecutive clean weekly or scheduled runs before general availability.

If launch timeline cannot wait three weeks, run three scheduled/manual staging runs on separate days and document the caveat.

### 11.3 Add LLM planner eval harness

Create a small fixed dataset:

- transcript words;
- visual manifest summaries;
- user prompt/style;
- expected constraints.

Assertions:

- valid JSON/schema;
- no negative durations;
- no overlapping cuts unless allowed;
- target duration sane;
- b-roll requests valid;
- semantic quality baseline if feasible.

### 11.4 Add golden tests for AI visual features

Especially:

- background segmentation/cutout truth;
- auto-reframe stability;
- caption timing;
- render output sanity.

### 11.5 Wire actual SLO/cost metrics

Use existing SLO/heartbeat/cost tables if present. Do not invent new tables before checking.

Track:

- provider latency;
- provider failures;
- queue depth;
- job duration;
- retry counts;
- cost per workspace/provider;
- fallback/degraded-mode counts.

## Verify

- Gated workflow runs successfully with staging secrets.
- Planner evals run and fail on known-bad fixture.
- Metrics visible in logs/table/admin path.

---

# Phase 12 — Security, Privacy, Retention, and Secret Logging

**Priority:** P1  
**Goal:** Prevent secret leakage, document third-party data flow, and enforce data retention.

## Why

The AI audit flags provider data leakage risks: audio, images, transcripts, prompts, and visual summaries are sent to third parties. It also flags indefinite storage and possible token leakage in logs.

## Scope

Expected files:

- API logging/error handling
- worker logging/error handling
- provider adapters for Groq/OpenRouter/Runway
- `apps/api/app/services/retention.py`
- storage services
- docs such as `DATA_HANDLING.md`

## Tasks

### 12.1 Audit provider logging

Find every place provider exceptions are logged. Ensure logs do not include:

- API keys;
- bearer tokens;
- signed URLs;
- raw uploaded media paths if sensitive;
- full transcript/prompt bodies unless explicitly safe and redacted.

### 12.2 Add log redaction utility

Centralize redaction for:

- `Authorization` headers;
- `api_key`, `token`, `secret` fields;
- common provider key prefixes;
- signed URL query tokens.

### 12.3 Add secret-leak tests

Trigger fake provider failures with secret-shaped strings and assert captured logs are redacted.

### 12.4 Define retention policy

**DECISION NEEDED:** retention durations.

Data classes:

- raw uploads;
- extracted audio;
- thumbnails/frames;
- transcripts;
- visual manifests and embeddings;
- generated b-roll;
- exports;
- provider raw payloads;
- telemetry/preferences.

### 12.5 Implement retention/purge extension

Check `retention.py` before adding new logic. Extend existing cleanup jobs rather than duplicating.

### 12.6 Write `DATA_HANDLING.md`

Document actual data flows:

- Groq: extracted audio and transcription request metadata;
- OpenRouter: transcript words, prompt, visual/audio summaries, style instructions;
- Runway: prompt text, prompt image/reference URL;
- stock providers: search terms and media metadata;
- future providers as added.

## Verify

- Secret-redaction tests pass.
- Retention job test deletes or expires expected data.
- `DATA_HANDLING.md` matches actual code paths.

---

# Phase 13 — Final Production Readiness Gate

**Priority:** Gate  
**Goal:** No production-ready claim until all checks pass with evidence.

## Required evidence

### Security gate

- [ ] Production boot fails with `SKIP_API_AUTH=true`.
- [ ] Production boot fails with `SKIP_AUTH_MIDDLEWARE=true`.
- [ ] Production boot fails with mock/header identity enabled.
- [ ] Production deploy manifests contain no truthy bypass defaults.
- [ ] Human security review completed.

### Schema gate

- [ ] Canonical migration source decided and documented.
- [ ] Non-canonical tree archived or generated mirror only.
- [ ] Fresh DB bootstrap from canonical migrations passes.
- [ ] RLS/schema tests pass.
- [ ] Backup/restore verified against consolidated schema.

### Async/workers gate

- [ ] Plan generation is async end to end.
- [ ] Heavy operations do not execute inline in API in production.
- [ ] Missing worker/Redis returns explicit failure, not local fallback.
- [ ] Worker health checks pass.

### Memory/render gate

- [ ] Remotion exports stream to storage.
- [ ] Large-file staging test passes.
- [ ] Largest supported export stays below 80% of worker memory limit.

### AI truthfulness gate

- [ ] Background-removal UI matches implementation.
- [ ] If real segmentation is claimed, golden visual test proves real mask behavior.
- [ ] Fixture/local/mock providers are visibly distinguishable from live providers.

### GPU gate

- [ ] GPU concurrency cap verified.
- [ ] Injected CUDA OOM fails one job, not worker process.
- [ ] Queue backlog remains observable.

### Provider/cost gate

- [ ] Quota guard prevents over-limit Runway/OpenRouter calls.
- [ ] Provider usage is recorded.
- [ ] Live provider contract tests pass.
- [ ] At least 3 clean gated provider-test runs completed or caveat documented.

### Privacy/retention gate

- [ ] Secret log redaction tests pass.
- [ ] Data retention policy implemented and scheduled.
- [ ] `DATA_HANDLING.md` reviewed.
- [ ] Third-party provider data flow documented.

### End-to-end smoke gate

Run in production-like staging:

- [ ] auth/login;
- [ ] project create;
- [ ] upload source video;
- [ ] preprocessing complete;
- [ ] transcript ready;
- [ ] visual analysis ready or graceful unavailable state;
- [ ] async plan create and status polling;
- [ ] editor loads resulting plan;
- [ ] render create;
- [ ] export upload/download;
- [ ] admin/ops visibility;
- [ ] cleanup/retention job observed.

### Audit trail gate

- [ ] `REMEDIATION_LOG.md` contains evidence for every completed task.
- [ ] Unverified items are explicitly marked, not hidden.
- [ ] Human reviewers sign off security-sensitive phases.

Only after this phase passes should Motionly be described as production-ready.

---

# Phase 14 — Post-Launch AI Capability Backlog

These are valuable but should not preempt hardening work.

| Capability | Priority | Prerequisites | Notes |
|---|---:|---|---|
| Silence and filler-word removal | P0 enhancement | Stable transcript/timeline pipeline | Use Whisper word timestamps to trim pauses, “uh,” “um,” etc. |
| Kalman-filter auto-reframe | P1 | Visual tracking tests | DeepSeek correctly maps this to audit recommendation; can be pulled earlier if auto-reframe is core launch feature. |
| WhisperX translation alignment | P1/P2 | Caption translation launch scope | Not a launch blocker unless translation is marketed at launch. |
| AI thumbnails and titles | P1 | Cost guards + eval framework | Use key frames + transcript; requires image provider review. |
| Multilingual voice dubbing | P1 | Privacy/cost review | ElevenLabs/Cartesia-style provider; needs data handling update. |
| Virality/hook scoring | P2 | LLM eval framework | Avoid unverified scoring claims without benchmarks. |
| Copyright/audio-ID match | P2 | Provider privacy review | ACRCloud-style integration. |
| Kubernetes/HPA autoscaling | Infra decision | Confirm Kubernetes target | Do not implement from remediation plan alone. |

---

# Suggested Execution Order

This is the recommended order if a team wants fastest risk reduction:

1. Phase 0 rules and `REMEDIATION_LOG.md` setup.
2. Phase 1 auth bypass hard-fail.
3. Phase 2 async edit planner.
4. Phase 3 stream render uploads.
5. Phase 4 Option B relabel/disable fake background removal unless Option A is explicitly approved.
6. Phase 5 migration consolidation.
7. Phase 6 production fallback removal and worker-owned heavy jobs.
8. Phase 7 AI error isolation.
9. Phase 8 GPU OOM controls.
10. Phase 9 planner cache/fallback correctness.
11. Phase 10 cost controls.
12. Phase 11 contract tests/evals/observability.
13. Phase 12 privacy/retention/secret logging.
14. Phase 13 readiness gate.
15. Phase 14 backlog only after launch hardening.

---

# Non-Negotiables

- Do not ship production with auth bypass or mock identity paths enabled.
- Do not let the API thread perform long-running AI/render/media jobs in production.
- Do not buffer full rendered videos in worker memory.
- Do not market CSS ellipse/blur as AI background removal.
- Do not keep two hand-edited migration sources.
- Do not add Kubernetes/HPA unless deployment substrate is confirmed.
- Do not call paid providers without quota checks.
- Do not log secrets, signed URLs, or raw provider payloads without redaction.
- Do not mark a task done without verification evidence.

