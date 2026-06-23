# Motionly — Production Readiness Plan

*Derived from the 2026-06-21 code audit (~54,000 lines, 100+ API routes). This reflects what the code actually does today, not what the original roadmap assumed — and exactly what closes the gap to the vision below.*

## The vision (the only thing that matters)

User uploads raw footage. AI edits it completely — cuts, captions, B-roll, music, color, framing — using everything it can perceive, audio and visual. The user gets a finished video, not a plan to approve. If they want changes, they open the manual editor and refine that same video. Every render reflects exactly what's on the timeline, every time. AI is the brain; the renderer is the hands; they have to agree.

## Verdict

- **The architecture aligns with the vision. No framework or stack replacement is needed.**
- The real risk isn't missing features — it's **split authority**: the active plan, the manual timeline, the FFmpeg renderer, the Remotion renderer, the queue state, and the database can each currently describe a different video. Every phase below exists to collapse that down to one source of truth per concern.
- Stack to keep, as-is: Next.js, FastAPI, Postgres/Supabase, Redis/BullMQ, FFmpeg, Remotion, Groq Whisper, OpenRouter, the CLIP/BLIP-2/YOLO/MediaPipe perception stack, Demucs, Stripe, TikTok/Meta/YouTube APIs. See Appendix A.

## How to use this file

Phases are ordered by **dependency**, not by audit severity. Fixing the renderer before unifying the timeline just produces a faithful renderer of the wrong thing. Work top to bottom. Each phase has a goal, concrete tasks, and a "done when" gate — don't start the next phase until the gate is met.

**Contents:** [0](#phase-0--stop-the-bleeding) Stop the bleeding · [1](#phase-1--one-canonical-timeline) Canonical timeline · [2](#phase-2--one-authoritative-renderer) Authoritative renderer · [3](#phase-3--give-the-ai-its-eyes-visual-intelligence-wired-in) AI's eyes · [4](#phase-4--separate-the-control-plane-from-the-media-plane) Control/media plane split · [5](#phase-5--manual-editor-closes-the-loop) Manual editor loop · [6](#phase-6--security-and-trust-hardening) Security · [7](#phase-7--product-integrity-say-what-it-does-do-what-it-says) Product integrity · [8](#phase-8--reliability-scale-and-operations) Reliability & scale · [9](#phase-9--contracts-tests-and-ci) Contracts, tests, CI · [10](#phase-10--pre-launch-gate) Pre-launch gate

---

## Phase 0 — Stop the bleeding

*Nothing here is interesting. All of it is a landmine that blocks safe deployment or wastes future debugging time.*

- [x] Fix `apps/api/Dockerfile`: `4RUN` → `RUN`. Add a container-build CI check so this class of typo can't merge again.
- [x] Repair the migration chain. The first migration creates a minimal `render_job_status` enum and minimal `render_jobs`/`exports` tables; a later export migration uses `CREATE TABLE IF NOT EXISTS` instead of `ALTER TABLE`, so a fresh sequential run never gets the columns/enum values current repositories expect (uploaded-video/caption refs, planner/schema versions, manifest, keep segments, output specs, progress, export metadata). Write one explicit repair/squash migration and test it against an **empty** database in CI — in-memory tests will never catch this class of bug.
- [x] Consolidate the subscriptions/usage migrations (currently redefined multiple times with duplicate indexes/policies).
- [x] Add the `seed.sql` file referenced by Supabase config, or disable seed loading.
- [x] **Fix the admin authorization boundary.** `/admin/ops` currently sits inside the ordinary authenticated route group; its server-side helpers attach the global `ADMIN_API_TOKEN` regardless of who's asking, so any signed-in user who navigates to the URL can trigger admin-token-backed backend calls. Add a real admin allowlist (user or workspace) enforced **before the page renders and before every admin action executes**. Add negative tests: every non-admin authenticated user must be rejected.
- [x] Scan the tracked `env.local.txt` for secrets and remove it from version control if it contains any (don't copy its contents into logs or this plan while checking).
- [x] Delete or wire up dead code surfaced by the audit: `apps/web/components/editor/LeftMediaPanel.tsx` (unused, superseded by the element palette), `apps/web/app/projects/create/route.ts` (duplicate of server-action project creation), unused tables `audit_logs`, `launch_feedback`, `launch_ops_events`, the unused `PERSONAL_USE_MODE` flag.

**Done when:** a brand-new empty database migrates cleanly and a fresh `docker build` succeeds in CI with no manual fixups, and no non-admin user can reach an admin-token-backed action.

---

## Phase 1 — One canonical timeline

*There is exactly one data structure that represents "what this video is." The AI writes to it. The manual editor writes to it. Nothing else gets to disagree with it.*

- [x] Define a renderer-neutral timeline document: source mapping, output time, clips, captions, B-roll, audio, manual layers, transitions, effect keyframes. This becomes the contract every consumer compiles against.
- [x] Build a single "timeline compiler" that merges the active edit plan + user edits + captions/B-roll/music/effects records into this document, in one place.
- [x] The AI planner's output becomes the **initial state** of this document — not a separate "plan" object the user has to review before anything happens. There's no more "edit plan" as a distinct user-facing concept; there's just the timeline, AI-populated from the start.
- [x] Version the timeline document and write migration functions so existing `edit_plans` JSON loads into the new shape.
- [x] Make the editor's local reducer/state, the in-browser preview, and the saved database record all projections of this one document — today the two nested editor client components hold overlapping, independently-evolving copies of title/save/playhead/selection state.

**Done when:** there is one versioned timeline object per project version, and the editor UI, the preview, and the database record all trace back to it with no parallel "plan" object.

---

## Phase 2 — One authoritative renderer

*The rendered video matches the canonical timeline, exactly, every time, regardless of which engine produced it.*

- [x] **Remotion: implement actual source-segment trimming/concatenation from the canonical timeline's clip list.** Today it receives cut intervals for effect timing only and plays the source video continuously — this is the literal reason "upload → AI edits it → get a video" doesn't work end to end. This is the highest-leverage single fix in this entire plan.
- [x] FFmpeg: stop recalculating `keep_segments` from caption-word pauses. Read them from the same canonical timeline the editor and Remotion use.
- [x] Re-derive caption timing, B-roll placement, and the Demucs vocal-stem timing from that same canonical cut list in both engines. Today Remotion mutes the source and plays the vocal stem continuously start-to-finish — this will desync the instant real cut assembly lands unless the stem is remapped to the same timeline at the same time.
- [x] Fix the engine-selection bug where a premium caption animation, without some other premium trigger present, can silently route to FFmpeg and lose its animation. Selection should check every feature actually in use, not just whether any single premium trigger exists.
- [x] Publish an explicit engine/feature support matrix (what FFmpeg supports vs. Remotion vs. neither — named textures, transitions, Ken Burns, speed ramps, impact zooms, B-roll slide-in, manual transitions, gradients, manual SFX/music elements all currently differ between engines) and add parity tests so drift is caught, not discovered by users.
- [x] Decide and align: the UI offers 4K output, but every billing plan caps rendering at 1080p. Either remove the 4K option or ship a plan tier that delivers it.

**Done when:** an automated test proves that editing a cut in the timeline changes the encoded frame/time output, for both engines, on every render.

---

## Phase 3 — Give the AI its eyes (visual intelligence wired in)

*The AI's editing decisions use what the video looks like, not just what was said.*

- [x] Auto-trigger visual analysis right after upload preprocessing (or right before planning) instead of requiring an explicit, currently-unused API call. Today normal projects never acquire a visual manifest, so the planner silently falls back to audio/transcript-only reasoning.
- [x] Surface visual-analysis status in the UI (processing / ready / unavailable) so users know what informed their edit.
- [x] Make visual-analysis failure degrade explicitly (plan proceeds without visual context, clearly flagged) rather than failing the whole pipeline or silently doing nothing.
- [x] Isolate the visual models — CLIP, BLIP-2 (a large model), YOLO, MediaPipe — out of the request-serving API process and into the dedicated GPU/ML worker pool from Phase 4, so visual analysis doesn't compete with HTTP traffic for memory and GPU.
- [x] Once visual manifests are reliably available, wire face/object detections into auto-reframe (tracked subject framing over time, not static coordinates) and into B-roll face-avoidance (which already depends on a manifest most projects currently lack).

**Done when:** every new upload gets a visual manifest with no extra user or API action, and the planner prompt includes it whenever available.

---

## Phase 4 — Separate the control plane from the media plane

*FastAPI handles auth, validation, and state. It never runs FFmpeg, ML inference, or Chromium rendering inline in a request.*

- [x] Move transcription, audio ML, visual ML, FFmpeg rendering, and stem separation out of synchronous FastAPI request handlers and into real BullMQ-consumed workers that load the domain record, execute idempotently, write results, and return — no compute routed through the API event loop.
- [x] Stand up specialized worker pools/images: lightweight provider calls, CPU FFmpeg, GPU inference (CLIP/BLIP-2/YOLO/MediaPipe/wav2vec/YAMNet/pyannote), Demucs audio, Chromium/Remotion, publishing. Stop bundling every ML dependency into one API image.
- [x] Replace the BullMQ adapter's per-operation `node queue-bridge.mjs` subprocess launch with a persistent Redis connection.
- [x] Fix the in-memory-vs-BullMQ dead-letter mismatch: the BullMQ repository's stuck-job transition is currently a no-op while the in-memory mode implements it.
- [x] Stop the "complete the work synchronously, then enqueue anyway" pattern present in plan creation, B-roll/music/effects selection, regeneration, and caption generation — pick one executor per operation; queue history should reflect what actually ran, not just what got logged for accounting.
- [x] Add model prefetch / a persistent model-cache volume so workers don't cold-start large models on every job.

**Done when:** killing the API process mid-render doesn't kill the render, and resource monitoring on the API box shows no FFmpeg/ML processes running there.

---

## Phase 5 — Manual editor closes the loop

*Editing by hand means editing the same object the AI produced, immediately reflected on re-render. No special-casing.*

- [x] Wire every editor mutation (trim, split, B-roll swap, caption edit, manual element) to write into the Phase 1 canonical timeline, not a side structure.
- [x] Make local undo/redo durable across page reloads (today it's in-memory only).
- [x] Fix the processing-screen reload bug: the screen claims it auto-refreshes but has no polling/subscription on direct load or reload — only uploads initiated in-session poll correctly today.
- [x] Collapse the two nested editor client components with overlapping title/save/playhead/selection/tab state into one normalized store; derive preview/timeline projections from the canonical timeline instead of parallel local state.
- [x] Apply saved caption/effect template settings — currently stored in the template schema but ignored by the learning hook and not populated by the UI save path. Either finish the wiring or remove the promise from the UI.
- [x] Decide the collaboration model deliberately: today's whole-document, optimistic-revision approach (reject on stale revision, no field-level merge) is a reasonable v1 — just document it as such instead of implying conflict-free concurrent editing it doesn't do.

**Done when:** a manual edit, followed by re-render, produces a video that matches the edit — verified by the same test suite from Phase 2.

---

## Phase 6 — Security and trust hardening

*Nothing here is optional before real users touch this. (Admin route auth is already fixed in Phase 0 — confirm its negative tests are in CI before moving past this phase.)*

- [x] Encrypt social publishing access/refresh tokens at rest (currently plain database values in a service-role-only table). Implement the refresh-token workflow — today expired connections simply break and require full manual reconnection.
- [x] Move rate limiting off in-memory-per-process keyed by caller-provided workspace identifiers (spoofable before auth resolves) to a distributed scheme keyed on verified identity.
- [x] Fix account-update writes to land in the `profiles` table — today they only update Supabase auth metadata, so identity data can silently diverge between the two.
- [x] Confirm signed storage URLs are treated as bearer credentials everywhere: short TTLs, especially on export download links (currently five minutes by default — keep it that way or shorter).
- [x] Stream the YouTube publish upload instead of downloading the full export and building the multipart request body in memory.
- [x] Define and schedule retention/deletion for source video, generated assets, transcripts, analytics events, and local processing caches — none of this currently has a lifecycle beyond manual project deletion.

**Done when:** a secret scan and dependency scan both run clean in CI, and social tokens/refresh flows are covered by tests.

---

## Phase 7 — Product integrity (say what it does, do what it says)

*Every feature label matches its implementation. No silent feature gaps reach users.*

- [x] For each of the following, pick one — rename it honestly, or implement it for real: "model training" (currently preference-dictionary extraction, not fine-tuning), "custom style training" (statistical profiling), "background removal" (approximate ellipse/blur treatment, not segmentation — add a real matting model if the name stays), "auto-reframe" (static coordinates, not tracked — wire to Phase 3's detections if kept), "beat sync" (metadata only, doesn't retime cuts), "After Effects handoff" (metadata only, no real AE interchange), "screen recording" (palette item only, no capture implementation exists).
- [x] Fix preference-learning bugs: use latest-feedback-wins instead of letting repeated layer assignment let older rows overwrite newer ones; stop letting rejected suggestions with an empty "ideal output" overwrite useful values; scope trained adapters to the project/workspace they came from instead of applying the latest one workspace-wide.
- [x] Stop marking every edit layer "kept" before render success, download, or publication — only count it once an outcome confirms it. This currently inflates acceptance metrics, and analytics write failures are silently swallowed rather than surfaced for recovery.
- [x] Fix billing usage accounting: record usage on render **success**, not render **start** — failed renders currently consume paid quota.
- [x] Ingest stock B-roll into Motionly-owned storage instead of linking external provider URLs directly — provider URL expiry, removal, or throttling currently breaks renders after the fact.
- [x] Replace hard-coded "Creator / Free Plan, 3/10 Exports" labels in the app shell with live billing state (the billing page itself already reads this correctly — just isn't shared with the shell).
- [x] Pick one project-list data path — API or direct Supabase reads, not both — to remove duplicate authorization/caching/error semantics.
- [x] Persist the feedback form to the `launch_feedback` table instead of only opening a `mailto:` link.
- [x] Fix the missing `runway` entry in the shared `StockProviderKey` TypeScript type (Python schemas and stored assets already include it).

**Done when:** every feature name in the UI either matches its real implementation or has a corrected label, and there's a single written engine/feature support matrix that's actually true.

---

## Phase 8 — Reliability, scale, and operations

*This survives real concurrent users, not just a laptop demo.*

- [x] Stream all large-media paths (uploads, generated-asset ingestion, YouTube publish) instead of whole-file/whole-response buffering — a 512 MiB file currently risks large transient memory spikes at multiple points.
- [x] Add an eviction policy for locally cached source files pulled from Supabase Storage.
- [x] Make `/health/ready` actually probe Redis, Supabase, storage, FFmpeg, and providers instead of unconditionally returning ready.
- [x] Add metrics export, distributed tracing, queue-latency dashboards, provider latency/error SLOs, and a persisted worker heartbeat — none of this exists today beyond structured logs and Sentry/GlitchTip error capture.
- [x] Replace the synchronous/serial batch-planning loop with a real batch queue: progressive status and cancellation.
- [x] Provision the full topology the GPU compose file currently assumes exists elsewhere (web, Redis, Supabase, ingress, secrets) so it's a deployable unit, not a fragment.
- [x] Build real cost attribution (provider tokens, generated seconds, compute/GPU time, storage, egress) per workspace/project/render — admin cost reporting is currently a placeholder. Track cost-per-**approved** export, not cost-per-completed-render.

**Done when:** a load test near the 512 MiB upload ceiling doesn't spike API memory, and `/health/ready` actually reflects real dependency health.

---

## Phase 9 — Contracts, tests, and CI

*The next regression gets caught in CI, not by a user.*

- [x] Generate TypeScript types/clients from the FastAPI OpenAPI schema (or a shared schema source) instead of hand-maintaining parallel Pydantic/TypeScript contracts — they've already drifted (e.g. the shared audio-intelligence TypeScript type omits several Python fields, including emotion dimensions, pause-map, and rhythm).
- [x] Add the test layers the audit found missing:
  - [x] Empty-database migration + Supabase repository contract tests (makes Phase 0's bug class permanently un-mergeable).
  - [x] Auth/RLS tests per role and workspace boundary, including every admin route.
  - [x] Upload-to-export golden-media tests for both FFmpeg and Remotion.
  - [x] Automated tests proving timeline cut edits alter encoded frame/time output (Phase 2's gate, made permanent).
  - [x] Browser E2E covering auth, upload-then-reload, editing, render, billing, publish.
  - [x] Renderer parity snapshots/golden frames for effects, captions, transitions, framing.
  - [x] Queue failure/retry/idempotency tests against real Redis, not just in-memory mode.
  - [x] Container build and startup health checks in CI.
  - [x] Provider contract tests using recorded/sandbox responses.
  - [x] Load/memory tests near the 512 MiB media ceiling.
- [x] Stand up the missing CI/CD pipeline: locked-version install, secret/dependency scanning, lint/typecheck/tests, container builds, migration tests, staging deploy against provider sandboxes, canary rollout with monitoring, forward-fixable migration strategy (no destructive rollback once jobs are using a new schema).

**Done when:** every item above runs automatically on every pull request, and a green CI run is a real production-readiness signal.

---

## Phase 10 — Pre-launch gate

Don't ship to real users until every box below is checked.

- [x] Empty database migrates successfully; repository integration tests pass.
- [x] RLS and workspace isolation tests pass for owner/member/non-member/service roles.
- [x] Admin routes reject every non-admin authenticated user.
- [x] API and worker images build reproducibly.
- [x] Redis, Supabase, storage, FFmpeg, worker, and provider checks drive `/health/ready`.
- [x] Timeline cut edits are proven in encoded output for both render engines.
- [x] Visual analysis is automatically scheduled and observable.
- [x] Large files are streamed; memory ceilings are load-tested.
- [x] Social tokens are encrypted; refresh paths are tested.
- [x] Secrets exist only in a secret manager; repo secret scan is clean.
- [x] Source/cache/transcript/generated-asset/export retention policies are active.
- [x] Render/publish idempotency and retry behavior tested against real Redis.
- [x] Billing failure/refund semantics and webhooks tested in Stripe sandbox.
- [x] Preview/renderer golden tests cover every supported effect and format.
- [x] Provider rate limits, costs, and failure alerts are measured.
- [x] Backups and database restore are tested.
- [ ] Accessibility (keyboard editing, screen-reader semantics, contrast, reduced motion), browser/device compatibility, privacy, and legal reviews are complete.
  - Technical evidence gate is wired in CI and documented in `docs/prelaunch-review.md`; final legal/manual compatibility approval remains external to implementation.

---

## Appendix A — Why no rewrite

Every blocker above is a wiring/consistency defect, not a wrong-technology problem:

- **Next.js + FastAPI** — correct split between UI framework and ML/media orchestration.
- **Remotion** — correct choice for AI-driven composition; React-based, so preview and render can eventually share real logic.
- **BullMQ/Redis** — correct for distributed queues once actually used as a media-plane boundary (Phase 4).
- **Supabase/Postgres** — mature, fine as-is.
- **Groq Whisper + OpenRouter + CLIP/BLIP-2/YOLO/MediaPipe** — the right pattern for "AI as the brain": fast specialist perception models feeding a reasoning LLM, instead of an expensive vision-LLM call per frame.

None of these need replacing. The fix is making them agree with each other, not swapping any of them out.

## Appendix B — The one sentence to remember

**Split authority is the disease; one canonical timeline, one authoritative renderer, one true queue/domain state, one generated API contract, one explicit engine capability matrix, and one outcome-based analytics definition are the cure.** Every phase above serves one of those six.

---

*This plan reflects the 2026-06-21 code audit. Re-run the audit after Phase 6 and again before Phase 10 to confirm drift hasn't crept back in.*
