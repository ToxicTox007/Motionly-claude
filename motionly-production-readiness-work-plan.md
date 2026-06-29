# Motionly Production Readiness Work Plan

**Purpose:** This is the working checklist for making Motionly production-ready.  
**Source basis:** Engineering audit + production-readiness review.  
**Rule:** Do not mark Motionly production-ready because features are “implemented.” Mark it ready only when each feature works end-to-end, is tested, is observable, and has safe failure handling.

---

## How to Use This File

Work through the phases in order.

- **P0** = ship-stopper. Do not launch without it.
- **P1** = production reliability blocker. Must be done before broad launch.
- **P2** = hardening/polish. Can be done during beta if explicitly accepted.
- Every item must end with:
  - code merged
  - tests added
  - staging verified
  - logs/metrics checked
  - failure mode documented

---

# Phase 0 — Security Ship-Stoppers

## 0.1 Lock down `SKIP_API_AUTH`

**Priority:** P0  
**Area:** API security

### Problem
`SKIP_API_AUTH` can bypass FastAPI authentication if environment variables are misconfigured.

### Required fix
- Allow `SKIP_API_AUTH=true` only in local development.
- Treat any unknown or misspelled environment as unsafe.
- Crash startup if:
  - `SKIP_API_AUTH=true`
  - and `APP_ENV` is not exactly `development` or `local`
- Add CI test that boots app with production-like env and `SKIP_API_AUTH=true`.
- CI must fail if app starts successfully.

### Files to inspect
- `apps/api/app/core/config.py`
- `apps/api/app/api/dependencies.py`

### Done when
- [ ] Production cannot boot with `SKIP_API_AUTH=true`
- [ ] Staging cannot boot with `SKIP_API_AUTH=true`
- [ ] Unknown `APP_ENV` values fail closed
- [ ] CI test proves unsafe config exits non-zero

---

## 0.2 Remove silent JWT fallback

**Priority:** P0  
**Area:** API security

### Problem
If `supabase_jwt_secret` is missing, auth may fall back to trusting request headers directly. That is an undocumented auth bypass.

### Required fix
- Remove unsigned-header trust fallback.
- Missing JWT secret in non-local environments must fail closed.
- Protected routes must return `401` or app must fail startup.
- Never infer identity from unsigned headers.

### Files to inspect
- `apps/api/app/api/dependencies.py`
- `apps/api/app/core/config.py`

### Done when
- [ ] Missing JWT secret never results in a successful protected request
- [ ] Unit tests cover missing secret
- [ ] Unit tests cover invalid token
- [ ] Unit tests cover forged user/workspace headers

---

## 0.3 Enforce Stripe webhook signatures everywhere

**Priority:** P0  
**Area:** Billing security

### Problem
Webhook verification must not be skipped in local/test/staging paths. Skipped verification can become a payment-forgery vector.

### Required fix
- Verify Stripe signatures unconditionally.
- Test mode may use Stripe test keys, but must still verify signatures.
- Reject missing or invalid `Stripe-Signature`.

### Files to inspect
- `apps/api/app/api/billing.py`
- Stripe webhook handling code

### Done when
- [ ] Missing signature is rejected in every environment
- [ ] Invalid signature is rejected in every environment
- [ ] Duplicate webhook event is idempotent
- [ ] Checkout → webhook → entitlement sync works in staging

---

## 0.4 Route-level tenancy verification

**Priority:** P0  
**Area:** Auth, workspace isolation, data safety

### Problem
The audit says tenancy is enforced through RLS and API membership checks. Production requires proving this route by route.

### Required fix
Create a route-level matrix and tests for:

- `/projects`
- `/projects/{id}/uploads`
- `/projects/{id}/plans`
- `/projects/{id}/captions`
- `/projects/{id}/broll`
- `/projects/{id}/music`
- `/projects/{id}/effects`
- `/projects/{id}/renders`
- `/projects/{id}/publish`
- `/billing`
- internal worker routes

For every project-scoped route, test:

- valid user
- missing auth
- invalid JWT
- wrong workspace user
- missing workspace header
- forged workspace header
- forged project ID
- forged upload ID
- forged render ID
- forged export ID

### Done when
- [ ] Every protected route has cross-workspace tests
- [ ] Every project-scoped object is checked against workspace ownership
- [ ] Signed URL generation cannot expose another workspace’s assets
- [ ] Worker routes require valid worker token
- [ ] Direct database/RLS tests exist where practical

---

# Phase 1 — Product and Compliance Decisions

## 1.1 Decide and enable billing strategy

**Priority:** P0 before paid launch, P1 before private beta  
**Area:** Billing, entitlements

### Problem
Billing is implemented but can be disabled by default. If this ships accidentally, premium features may become free.

### Required fix
- Decide launch pricing and plan limits.
- Enable billing in staging.
- Verify checkout unlocks paid features.
- Verify downgrade blocks new premium actions.
- Verify free users cannot bypass server-side limits.

### Server-side entitlement matrix

| Feature | Free user blocked? | Paid user allowed? | Server-side check? |
|---|---:|---:|---:|
| B-roll generation | [ ] | [ ] | [ ] |
| Premium templates | [ ] | [ ] | [ ] |
| Premium Remotion render | [ ] | [ ] | [ ] |
| High-resolution export | [ ] | [ ] | [ ] |
| Publishing limits, if any | [ ] | [ ] | [ ] |

### Files to inspect
- `apps/api/app/api/billing.py`
- entitlement checks
- render creation route
- B-roll route
- premium template access

### Done when
- [ ] Billing enabled in staging
- [ ] Stripe test checkout works
- [ ] Webhook updates subscription
- [ ] Free users are blocked server-side
- [ ] Paid users can access paid features
- [ ] Downgrade behavior is clear and tested

---

## 1.2 Verify data-handling documentation

**Priority:** P2, but cheap and should be done early  
**Area:** Compliance, privacy

### Problem
The code sends data to external providers. The documentation must match actual provider usage.

### Required fix
Update `DATA_HANDLING.md` to include all external providers:

- Groq
- OpenRouter
- Runway
- Pexels
- Pixabay
- Stripe
- Meta Graph API
- YouTube/Google APIs
- TikTok APIs
- Supabase

For each provider, document:

- what data is sent
- when it is sent
- whether user media/transcripts are included
- retention assumptions
- failure behavior

### Done when
- [ ] Every external call site has a matching documentation entry
- [ ] Legal/customer-facing docs match actual code behavior
- [ ] No undocumented third-party data sharing remains

---

# Phase 2 — AI Plan Data Integrity

## 2.1 Add real schema validation for edit plans

**Priority:** P0  
**Area:** AI planning, data integrity

### Problem
A schema hash is not enough. Every AI-generated plan must be structurally validated before persistence.

### Required fix
Create canonical Pydantic/JSON schema validation for:

- cuts
- zooms
- highlights
- B-roll slots
- caption references
- layout metadata
- render dimensions
- aspect ratio
- timeline duration
- schema version
- prompt version

Validation must reject:

- malformed JSON
- missing required fields
- negative timestamps
- cuts outside source duration
- overlapping invalid cuts
- captions outside transcript range
- B-roll outside output timeline
- unsupported preset keys
- unsupported aspect ratios
- stale schema versions

### Files to inspect
- `apps/api/app/services/edit_planner.py`
- plan ingestion path
- `public.edit_plans` write path
- shared plan schema package

### Done when
- [ ] Invalid LLM output never reaches `public.edit_plans`
- [ ] Fallback triggers cleanly after validation failure
- [ ] Validation errors are logged safely
- [ ] Tests cover malformed JSON
- [ ] Tests cover invalid timestamps
- [ ] Tests cover bad overlaps
- [ ] Tests cover stale schema version

---

## 2.2 Retry LLM planning before deterministic fallback

**Priority:** P1  
**Area:** AI planning reliability

### Problem
A single malformed LLM response should not immediately degrade the project to a generic fallback plan.

### Required fix
Implement bounded retry flow:

1. Attempt normal LLM plan.
2. If invalid, retry with validation errors.
3. If still invalid, retry strict JSON-only minimal plan.
4. If still invalid, use deterministic fallback.

Persist metadata:

- `planner_source`
- `model_name`
- `prompt_version`
- `schema_version`
- `validation_errors`
- `retry_count`
- `fallback_reason`

### Done when
- [ ] One bad completion does not immediately create fallback
- [ ] Repeated bad completions create valid fallback
- [ ] Fallback plans are clearly marked
- [ ] All persisted plans are render-safe

---

# Phase 3 — AI and ML Reliability

## 3.1 CUDA OOM hardening

**Priority:** P1  
**Area:** GPU workers

### Problem
CLIP, BLIP-2, YOLOv8, MediaPipe, and other visual models can trigger CUDA OOM and crash workers.

### Required fix
- Catch CUDA OOM explicitly.
- Empty CUDA cache after OOM.
- Mark job retryable only when safe.
- Requeue with backoff.
- Prevent infinite retry loops.
- Keep worker process alive where possible.
- Record `cuda_oom` as structured failure reason.

### Files to inspect
- `apps/api/app/services/visual_intelligence.py`
- `apps/api/app/workers/visual_job.py`
- `apps/worker/src/worker.mjs`

### Done when
- [ ] Forced OOM does not permanently kill queue
- [ ] Worker stays alive or restarts cleanly
- [ ] Job is retried with backoff
- [ ] Retry limit is enforced
- [ ] Metrics show OOM count

---

## 3.2 Transcription confidence and quality gates

**Priority:** P2  
**Area:** Transcription, editor UX

### Problem
Poor transcripts can create bad edit plans.

### Required fix
Flag transcripts as low-confidence when:

- transcript is empty
- word count is suspiciously low
- repeated text dominates
- long silent gaps exist
- provider returns low confidence, if available
- language mismatch is detected
- audio extraction failed or partial

### Files to inspect
- `apps/api/app/services/transcription_jobs.py`
- transcript persistence code
- editor transcript UI

### Done when
- [ ] Low-confidence transcript is visible in editor
- [ ] Empty transcript blocks automatic planning
- [ ] User sees recoverable state
- [ ] Tests use noisy/silent audio fixtures

---

## 3.3 Network resilience for AI providers

**Priority:** P1  
**Area:** External providers

### Problem
Groq and OpenRouter are hard network dependencies.

### Required fix
- Add bounded timeouts.
- Add exponential backoff.
- Add circuit breaker for provider outages.
- Add provider-specific error mapping.
- Avoid queue-flooding retries during outage.

### Files to inspect
- `apps/api/app/services/transcription_jobs.py`
- `apps/api/app/services/edit_planner.py`
- provider client wrappers

### Done when
- [ ] Timeout does not hang job indefinitely
- [ ] Provider outage does not flood queue
- [ ] Failed job has clear user-facing reason
- [ ] Provider latency and failure metrics exist

---

# Phase 4 — GPU and Media Worker Architecture

## 4.1 Move matte extraction to dedicated GPU/media worker

**Priority:** P0  
**Area:** Worker architecture, performance

### Problem
MediaPipe selfie segmentation plus FFmpeg alphamerge is expensive and can block unrelated work.

### Required fix
- Create separate queue or worker pool for matte extraction.
- Keep it separate from normal domain jobs.
- Keep it separate from non-background-removal visual analysis if needed.
- Limit concurrency.
- Cache generated mattes by:
  - upload ID
  - source file hash
  - segmentation settings
  - model version
- Store matte status:
  - `pending`
  - `processing`
  - `completed`
  - `failed`
  - `skipped`

### Files to inspect
- `apps/api/app/services/visual_intelligence.py`
- `apps/worker/src/worker.mjs`
- `apps/api/app/workers/visual_job.py`
- queue routing code

### Done when
- [ ] Matte jobs do not delay transcription/planning jobs
- [ ] Matte jobs have their own concurrency limit
- [ ] Matte cache prevents duplicate work
- [ ] Render can detect matte availability
- [ ] User can recover from matte failure

---

## 4.2 GPU autoscaling and memory ceilings

**Priority:** P1  
**Area:** Infrastructure

### Problem
GPU worker resource limits need to be explicit before scale.

### Required fix
Document and implement:

- GPU worker instance type
- max concurrent jobs per GPU
- memory ceiling
- queue-depth scaling rule
- cooldown period
- max worker count
- OOM alert threshold
- long-job timeout

### Done when
- [ ] Autoscaling policy exists
- [ ] Load test exercises scale-up
- [ ] Load test exercises scale-down
- [ ] OOM alerts fire in test
- [ ] Runbook explains GPU worker failure recovery

---

# Phase 5 — B-Roll Semantic Quality

## 5.1 Add CLIP embedding enrichment for stock candidates

**Priority:** P0  
**Area:** B-roll quality

### Problem
Pexels and Pixabay do not provide embeddings, so real B-roll quality falls back to lexical matching.

### Required fix
Create background enrichment job:

1. Fetch stock candidate.
2. Download thumbnail or representative frame.
3. Run same CLIP embedding pipeline used for visual intelligence.
4. Store embedding against `stock_assets`.
5. Cache by provider asset ID.
6. Track license/source metadata.

### Files to inspect
- `apps/api/app/services/stock_video.py`
- `apps/api/app/services/visual_intelligence.py`
- `stock_assets` persistence code

### Done when
- [ ] Real Pexels/Pixabay results get embeddings
- [ ] Embeddings are cached
- [ ] Failed enrichment falls back safely
- [ ] License/source metadata is preserved

---

## 5.2 Prefer embedding ranking over lexical ranking

**Priority:** P0  
**Area:** B-roll relevance

### Problem
Even after embeddings exist, ranking must actually use them.

### Required fix
Ranking order:

1. embedding cosine similarity
2. orientation match
3. duration fit
4. keyword relevance
5. visual diversity
6. provider/license confidence

Lexical matching should only be fallback.

### Done when
- [ ] Candidates with embeddings outrank lexical-only matches when semantically better
- [ ] Adjacent slots avoid duplicate-looking clips
- [ ] Low-confidence B-roll is labeled in editor
- [ ] Tests compare fixture and real-provider metadata behavior

---

# Phase 6 — Render and Preview Correctness

## 6.1 Fix matte preview/render parity

**Priority:** P1  
**Area:** Editor, render trust

### Problem
Editor preview uses a static CSS ellipse but final render uses real alpha-keyed MediaPipe output.

### Required fix
- Generate low-resolution real matte preview.
- Use real matte preview in editor when available.
- Show clear “approximate preview” state before matte is ready.
- Ensure off-center subjects preview correctly.

### Files to inspect
- `apps/web/components/editor-client.tsx`
- `apps/api/app/services/visual_intelligence.py`
- matte generation pipeline

### Done when
- [ ] Off-center subject is previewed correctly
- [ ] Preview and render use same matte source when available
- [ ] Approximate mode is labeled
- [ ] Visual test covers matte preview

---

## 6.2 Fix timeline frame-rate drift

**Priority:** P1  
**Area:** Editor/render consistency

### Problem
Browser playback timing can drift from Remotion’s exact frame rate.

### Required fix
- Create one canonical timeline conversion utility.
- Share FPS constants between frontend and renderer.
- Drive cuts/captions/B-roll/zooms from frame-accurate mapping.
- Test boundary frames.

### Files to inspect
- `apps/web/components/editor-client.tsx`
- Remotion render config
- timeline utilities
- shared package

### Done when
- [ ] Cut at frame N in editor lands at frame N in render
- [ ] Caption boundaries differ by no more than one frame
- [ ] B-roll boundaries differ by no more than one frame
- [ ] Tests cover cut boundaries and final frame

---

## 6.3 Add Chromium worker-image smoke test

**Priority:** P1  
**Area:** Render infrastructure

### Problem
Premium Remotion rendering requires Chrome/Chromium inside worker image.

### Required fix
- Add CI smoke test that boots worker image.
- Run trivial Remotion render.
- Fail build if Chromium missing or unusable.

### Files to inspect
- worker Dockerfile
- `apps/worker/src/remotion.mjs`
- Remotion config

### Done when
- [ ] CI catches missing Chromium
- [ ] Smoke render produces valid output
- [ ] Base-image changes cannot silently break premium render

---

## 6.4 Render preflight, timeout, cancellation, and failure reasons

**Priority:** P1  
**Area:** Render reliability

### Problem
Render jobs are expensive and must fail early when inputs are invalid.

### Required fix
Before render starts, verify:

- source video exists
- transcript/captions are valid
- ASS captions compile for FFmpeg path
- Remotion props validate
- B-roll assets are downloadable
- music assets are downloadable
- FFmpeg exists
- Chromium exists for premium render
- temp directory is writable
- export bucket is writable

Add:

- render timeout
- cancellation support
- retry classification
- user-readable failure reason
- structured render progress events

### Done when
- [ ] Invalid render inputs fail before expensive processing
- [ ] Timeout stops runaway render
- [ ] User can cancel queued/running render where safe
- [ ] Failed render shows clear reason
- [ ] Retried render does not duplicate exports unexpectedly

---

# Phase 7 — Publishing Reliability

## 7.1 OAuth token auto-refresh

**Priority:** P1  
**Area:** Publishing

### Problem
Publishing can fail because of stale tokens.

### Required fix
- Store token expiry metadata.
- Refresh tokens before use.
- Add scheduled refresh for near-expiry tokens.
- Detect revoked refresh tokens.
- Show reconnect state to user.

### Files to inspect
- `apps/api/app/services/publishing.py`
- `apps/api/app/services/token_cipher.py`
- `publish_connections` table

### Done when
- [ ] Token expiring soon refreshes before publish
- [ ] Revoked token shows reconnect requirement
- [ ] Refresh failure is logged safely
- [ ] Access/refresh tokens never appear in logs

---

## 7.2 Platform-specific rate-limit and error handling

**Priority:** P2  
**Area:** Publishing

### Problem
TikTok, Meta, and YouTube have different rate limits and failure behavior.

### Required fix
Map provider errors:

- expired token
- revoked token
- insufficient scope
- rate limit
- unsupported video format
- account permission issue
- upload failed
- processing failed
- duplicate publish risk

### Done when
- [ ] Simulated 429 schedules retry
- [ ] Retry does not create duplicate post
- [ ] Insufficient scope asks for reconnect
- [ ] Provider-specific errors display clearly

---

# Phase 8 — Worker Reliability and Job Recovery

## 8.1 Add subprocess supervision

**Priority:** P1  
**Area:** Worker runtime

### Problem
Node worker spawns persistent Python subprocesses through stdin/stdout. A dead subprocess can stall jobs.

### Required fix
- Add subprocess health checks.
- Detect crashed Python subprocess.
- Restart with backoff.
- Requeue in-progress job safely.
- Emit structured worker event.

### Files to inspect
- `apps/worker/src/worker.mjs`
- `apps/api/app/workers/domain_job.py`
- `apps/api/app/workers/visual_job.py`

### Done when
- [ ] Killing Python subprocess does not stall queue
- [ ] Job is retried or failed safely
- [ ] Node worker recovers without manual restart
- [ ] Metrics show subprocess restart count

---

## 8.2 Job idempotency and stuck-state recovery

**Priority:** P0  
**Area:** Queue correctness

### Problem
Production workers will crash, retry, and duplicate work. Every job must be safe under retry.

### Required fix
For every job type, verify:

- duplicate job does not duplicate DB rows
- retry does not corrupt state
- stuck `processing` state is recoverable
- failed state is visible
- cancellation state is respected
- retries do not publish twice
- retries do not create duplicate paid usage records
- retries do not create duplicate exports unless versioned

Job types:

- preprocessing
- transcription
- visual analysis
- matte extraction
- edit planning
- caption generation
- B-roll enrichment
- music selection
- effects generation
- basic render
- premium render
- publish
- retention cleanup

### Done when
- [ ] Idempotency test exists for every job type
- [ ] Stuck jobs have recovery path
- [ ] Retry limits are enforced
- [ ] Publish retries cannot duplicate posts
- [ ] Render retries cannot produce confusing duplicate exports

---

# Phase 9 — Observability, Redaction, and Operations

## 9.1 Wire OpenTelemetry for production

**Priority:** P1  
**Area:** Observability

### Required fix
- Configure production OTEL exporter.
- Ensure traces show full path:
  - API request
  - queue enqueue
  - worker execution
  - provider call
  - DB write
  - storage write
- Add correlation IDs.

### Done when
- [ ] Real trace visible in tracing backend
- [ ] Failed project can be traced step-by-step
- [ ] Correlation ID appears across API and worker logs

---

## 9.2 Add dashboards and alerts

**Priority:** P1  
**Area:** Operations

### Dashboards
- upload success rate
- preprocessing duration
- transcription duration/failure rate
- visual analysis duration/failure rate
- edit planner fallback rate
- B-roll embedding enrichment failure rate
- B-roll confidence distribution
- render duration/failure rate
- publish success/failure rate
- queue depth
- worker heartbeat
- provider latency
- CUDA OOM count
- storage deletion failure count

### Alerts
- auth startup validation failure
- queue backlog too high
- render failures above threshold
- provider outage spike
- CUDA OOM spike
- publishing failure spike
- retention cleanup failure
- worker heartbeat missing

### Done when
- [ ] Dashboards exist
- [ ] Alerts fire in staging test
- [ ] On-call can diagnose failed project from metrics/logs/events

---

## 9.3 Secrets and log redaction tests

**Priority:** P0  
**Area:** Security, privacy

### Problem
Redaction exists, but production needs tests proving secrets never leak.

### Required fix
Add tests that logs redact:

- Authorization header
- signed URL query params
- OpenRouter API key
- Groq API key
- Stripe secret key
- Stripe webhook secret
- OAuth access token
- OAuth refresh token
- Supabase service role key
- worker admin token

### Files to inspect
- `apps/api/app/core/logging.py`
- provider clients
- publishing service
- billing service
- storage URL generation

### Done when
- [ ] Redaction tests exist
- [ ] No secret appears in test logs
- [ ] Error paths are covered, not only success paths

---

# Phase 10 — Feature Completeness Verification

## 10.1 Caption presets across both render engines

**Priority:** P1  
**Area:** Captions

### Required fix
Render test clip with every caption preset through:

- FFmpeg renderer
- Remotion renderer

Verify:

- style is applied
- position is correct
- timing is correct
- text does not overflow badly
- unsupported preset is rejected

### Done when
- [ ] Every caption preset renders in FFmpeg
- [ ] Every caption preset renders in Remotion
- [ ] Visual diff/golden-frame coverage exists

---

## 10.2 Music catalog population

**Priority:** P2  
**Area:** Music

### Problem
Music relies on DB table population.

### Required fix
- Automate catalog seed or ingestion.
- Verify fresh environment has music available.
- Add empty-catalog user-facing state.

### Done when
- [ ] Fresh staging environment has non-empty music catalog
- [ ] Music recommendation works
- [ ] Empty catalog does not crash editor/render

---

## 10.3 Full feature matrix re-verification

**Priority:** P0  
**Area:** Launch gate

### Required fix
Re-run every feature from the audit against fresh `main` build:

| Feature | Must verify | Pass? |
|---|---|---:|
| Signup/Auth | signup, login, JWT, workspace creation | [ ] |
| Project Creation | create/list/fetch/isolation | [ ] |
| Media Upload | signed URL, upload complete, queue trigger | [ ] |
| Preprocessing | metadata, thumbnail, audio extraction | [ ] |
| Transcription | word timestamps, failure handling | [ ] |
| AI Analysis | visual/audio manifest, GPU fallback | [ ] |
| Edit Planning | valid schema, fallback, timeline | [ ] |
| Captions | chunking, ASS, Remotion captions | [ ] |
| B-roll | real search, embeddings, ranking | [ ] |
| Music | catalog, recommendation, render mix | [ ] |
| Effects/Transitions | zooms, overlays, transitions | [ ] |
| Editor | preview, edit, undo/redo, save/reload | [ ] |
| Render/Export | FFmpeg, Remotion, storage export | [ ] |
| Publishing | OAuth, upload, polling, failure states | [ ] |
| Billing | checkout, webhook, entitlements | [ ] |
| Background Jobs | queue, retries, idempotency | [ ] |
| Ops/Observability | metrics, heartbeats, traces | [ ] |

### Done when
- [ ] Every row passes
- [ ] Remaining caveats are explicitly accepted product decisions
- [ ] No known P0 issue remains
- [ ] No known unowned P1 issue remains

---

# Phase 11 — Testing Gates

## 11.1 Unit tests

Must cover:

- auth config validation
- JWT verification
- workspace access helpers
- Stripe webhook verification
- entitlement checks
- plan schema validation
- timeline conversion
- caption chunking
- B-roll scoring
- token encryption/decryption
- redaction
- provider error mapping

Done when:
- [ ] Unit suite passes
- [ ] Security edge cases included
- [ ] Bad input tests included

---

## 11.2 Integration tests

Must cover:

- upload → preprocessing
- preprocessing → transcription
- transcription → edit plan
- visual analysis → style/B-roll
- edit plan → captions
- B-roll search → embedding enrichment → ranking
- music selection → render
- effects → render
- render → export bucket
- publish → provider mock
- checkout → webhook → entitlement

Done when:
- [ ] Integration suite passes in CI
- [ ] Provider failures are mocked
- [ ] Storage failures are mocked

---

## 11.3 Full pipeline E2E smoke test

**Priority:** P0

Must run:

1. Create user
2. Create workspace
3. Create project
4. Upload video
5. Complete preprocessing
6. Complete transcription
7. Complete visual analysis
8. Generate edit plan
9. Generate captions
10. Generate B-roll
11. Select music
12. Generate effects
13. Open editor
14. Save edits
15. Render FFmpeg export
16. Render Remotion export
17. Publish using sandbox/mock provider
18. Verify status in UI

Done when:
- [ ] This test is part of merge-gate CI
- [ ] Every stage consumes previous stage output
- [ ] Failure at any stage is visible and recoverable

---

## 11.4 Golden-frame visual regression tests

**Priority:** P1

Must cover:

- caption placement
- caption animation
- B-roll overlay
- zoom moment
- transition frame
- background keying
- final aspect ratio

Done when:
- [ ] Golden-frame tests run nightly or in render CI
- [ ] Deliberate visual regression fails the test
- [ ] Test outputs are reviewable

---

## 11.5 Load and failure tests

Must simulate:

- many simultaneous uploads
- queue backlog
- provider timeout
- provider outage
- Redis restart
- worker crash
- Python subprocess crash
- GPU OOM
- storage upload failure
- render timeout
- duplicate webhook
- duplicate job execution
- revoked OAuth token
- publishing rate limit

Done when:
- [ ] System recovers or fails cleanly
- [ ] No duplicate posts
- [ ] No corrupt project state
- [ ] No stuck jobs remain unresolved

---

# Phase 12 — Backup, Restore, Rollback, and Disaster Recovery

## 12.1 Database backup and restore drill

**Priority:** P1  
**Area:** Operations

### Required fix
- Verify automated database backups.
- Run restore drill into separate environment.
- Confirm restored app can read projects, users, subscriptions, and exports metadata.

### Done when
- [ ] Backup schedule documented
- [ ] Restore drill completed
- [ ] Restore time objective documented
- [ ] Restore point objective documented

---

## 12.2 Migration rollback strategy

**Priority:** P1  
**Area:** Deployment safety

### Required fix
For each migration:

- identify backward compatibility risk
- define rollback or forward-fix path
- test migration on staging data snapshot
- avoid destructive migration without backup

### Done when
- [ ] Migration runbook exists
- [ ] Latest migration tested on staging
- [ ] Rollback/forward-fix path documented

---

## 12.3 Deployment rollback checklist

**Priority:** P1  
**Area:** Launch safety

### Required fix
Create rollback steps for:

- web app
- API
- standard worker
- render worker
- GPU worker
- database migrations
- environment variable mistakes
- provider credential mistakes

### Done when
- [ ] Rollback tested in staging
- [ ] Worker rollback does not corrupt in-flight jobs
- [ ] Rollback owners are assigned

---

# Phase 13 — Deployment Path

## 13.1 Local production simulation

Run with:

- auth bypass disabled
- billing enabled
- production-like env validation
- real Redis
- Supabase staging/local equivalent
- provider mocks first
- full test suite

Exit criteria:
- [ ] all P0 security tests pass
- [ ] full pipeline test passes locally
- [ ] no unsafe local-only bypass is required

---

## 13.2 Staging

Deploy:

- web
- API
- standard worker
- render worker
- GPU/media worker
- Redis
- Supabase migrations
- storage buckets
- Stripe test mode
- provider sandbox/mocks

Exit criteria:
- [ ] signup works
- [ ] upload works
- [ ] processing works
- [ ] planning works
- [ ] editing works
- [ ] FFmpeg render works
- [ ] Remotion render works
- [ ] publishing mock/sandbox works
- [ ] billing test checkout works
- [ ] dashboards receive data
- [ ] alerts are tested

---

## 13.3 Private beta

Constraints:

- limit video length
- limit file size
- limit concurrent renders
- manually review failed jobs daily
- keep provider costs capped
- use conservative worker concurrency

Track:

- upload success rate
- transcription failure rate
- edit planner fallback rate
- B-roll low-confidence rate
- render failure rate
- average time to export
- publishing success rate
- GPU OOM count
- support tickets per project

Exit criteria:
- [ ] no auth/tenancy bugs
- [ ] no secret leakage
- [ ] render success rate acceptable
- [ ] no unrecoverable job states
- [ ] no duplicate publishing
- [ ] no entitlement bypass

---

## 13.4 Production launch

Before launch:

- enable production billing
- enable production OAuth apps
- enable retention jobs
- enable alerting
- verify backups
- verify rollback
- run production smoke test
- keep queue concurrency conservative

Exit criteria:
- [ ] launch checklist complete
- [ ] rollback plan tested
- [ ] on-call runbook ready
- [ ] production smoke test passes

---

# Final Go-Live Gate

Do not launch until all are true:

- [ ] Phase 0 closed
- [ ] Route-level tenancy matrix passes
- [ ] Billing and entitlements verified server-side
- [ ] AI plan schema validation deployed
- [ ] LLM retry/fallback produces only valid plans
- [ ] GPU worker reliability tested
- [ ] Matte extraction isolated
- [ ] B-roll embeddings work for real providers
- [ ] Preview/render drift addressed
- [ ] FFmpeg render verified
- [ ] Remotion render verified
- [ ] Render preflight, timeout, and failure states implemented
- [ ] OAuth refresh implemented
- [ ] Publishing retries cannot duplicate posts
- [ ] Worker subprocess supervision works
- [ ] Job idempotency verified
- [ ] Stuck-job recovery verified
- [ ] Redaction tests pass
- [ ] OTEL traces visible
- [ ] Dashboards and alerts exist
- [ ] Full pipeline E2E smoke test passes
- [ ] Golden-frame tests exist
- [ ] Backup and restore drill complete
- [ ] Rollback tested
- [ ] Feature matrix re-verified clean

---

# Final Definition of Production Ready

Motionly is production ready only when a real user can complete the full workflow from signup to published export, and every major failure path is safe, observable, recoverable, and tested.

The standard is not “the feature exists in code.”

The standard is:

- the feature works end-to-end
- the feature is protected by auth and tenancy checks
- the feature respects billing entitlements
- the feature has automated tests
- the feature has clear failure states
- the feature is observable in logs/metrics/traces
- the feature can recover from retries/crashes/provider failures
- the feature does not leak secrets
- the feature matches what the user sees in preview and final output

Until this file is complete, Motionly should not be considered fully production ready.
