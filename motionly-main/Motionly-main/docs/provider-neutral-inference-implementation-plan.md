# Motionly Provider-Neutral Inference Implementation Plan

**Status:** planning only; no application code is changed by this document. Revision 3.

**Repository audited:** `motionly-main/Motionly-main`. Paths are relative to it unless they start with `/`.

## Changelog

### Revision 3

1. **Object detection, diarization and stem separation stay, as any-provider capabilities.** Task 17 is now required. It ports the three existing integrations to `generic_http`/`async_job` templates, so any provider is added through registry config only. Parity with today's behavior is required, and differential tests against the archived adapters prove it before anything is deleted.
2. **Tier 1 grows** (Section 4.0):
   - The minimum slices of Task 15 (`generic_http`) and Task 16B (`async_job`, blocking mode) move into Tier 1, together with Task 17.
   - The order is 15 → 16A → 16B → 17, all before Task 19.
   - The rest of the old Task 15 and 16B scope becomes the new Task 28 in Tier 3.

   Section 4.0 explains why this cannot wait for a later tier.
3. **Deletion order** (Task 19): the vendor-specific adapter files are deleted only after Task 17's conformance and differential tests pass. Before that, they are archived under the Git tag `archive/pre-neutral-inference`, created at the start of Task 17.
4. **No-route fallbacks** are spelled out (Sections 3 and 4.1, Task 17): empty detections, heuristic speaker labels, no stem control.
5. **Mapping language** (Section 2.10) is split into the Tier 1 slice that the three ports use and the Tier 3 extensions (Task 28). The slice adds three elements that parity needs: a `concat` function, a `vars` initializer, and variable segments in extraction paths.
6. **Side effect** (Task 18): with `async_job` in Tier 1, `video.generation.*` can also be configured in Tier 1 when the provider's API fits the slice.
7. Blocking question 2 is answered and removed.

### Revision 2


1. **Revision policy** (Tasks 4, 7, 14, 20; invariants 7–8; Sections 2.3, 2.6, 2.9): remote routes may use `revision: provider-managed` if the route opts in, and this logs a warning. Strict pinning applies only to local and self-hosted models. Licence metadata is required only for local models. Data-processor metadata is a warning in dev and required in production. Paid routes with unknown pricing need a hard cap.
2. **Three tiers with gates** (Section 4). Dependency fixes:
   - Task 9 is split into 9A (Tier 1) and 9B (Tier 2).
   - Task 14 joins Tier 1.
   - Task 16 is split into 16A and 16B.

   Revision 3 sets the current tier of Tasks 15, 16B and 17 (Section 4.0).
3. **The legacy bridge is conditional** (Section 0.4). The default is no live users and a single clean cutover (Path B). The translator becomes Task 6L (Path A only).
4. **Transitional work:** Task 13 (the transitional adapter) is deleted.
5. **The mapping language is capped** (new Section 2.10). Tiers 1 and 2 ship none, and every allowed function is listed.
6. **Billing:** Task 10 records Decision B-1 (feature-keyed quotas), with a one-line impact analysis and the temporary flag `AI_QUOTA_SCOPE=provider`. Task 10 no longer adds a transcription plan quota; transcription is not plan-metered today.
7. **Cleanup:** proof item 10 is removed. Sections 5–7 and 9 now follow the tiers and the clean-cutover default.
8. **Corrections from re-checking the files:**
   - `apps/worker/.env.example` has no vendor variables.
   - The privacy page also lists Google Cloud Vision and LALAL.AI.
   - `DEPLOYMENT-GUIDE.md` is at the Git root.
   - The Section 2.2 example is now valid JSON.

**Decisions recorded from operator:**

- **Live users/data (earlier answer `1a`):** this revision assumes **no live production users or data** and plans a single clean cutover (Section 0.4, Path B). If that is wrong, Path A keeps the bridge. See question 1.
- **Object detection, diarization and stem separation (answered in revision 3):** keep all three and convert them to any-model, any-provider capabilities with templates. This is Task 17: required, Tier 1.
- **Secret storage and key scope (`3a`, unchanged):** global operator keys, encrypted with AES-256-GCM in Postgres under a deployment KEK. Workspace scope is prepared but disabled. Tier 1 uses secret files or a bundle; the encrypted database store is Tier 3 (Task 20).

## 0. Scope, constraints and implementation rules

### 0.1 Goal

Motionly must route every AI capability through a Motionly-owned, provider-neutral contract. An operator must be able to add any model, endpoint and credential through configuration, without a code change. The initial production deployment must work with remote APIs only and no GPU. A later self-hosted endpoint or operator GPU must be usable by changing configuration only.

The implementation covers the inference layer only. It does not redesign authentication, billing plans, project editing, media storage, rendering, music licensing, stock-search providers, or unrelated workflow behavior except where those systems currently contain inference-provider names or inference job state.

### 0.2 Non-negotiable invariants

1. Application code calls a typed Motionly capability, never a vendor SDK or vendor-named helper.
2. Provider-specific behavior exists only in an adapter implementation or a documented declarative example. The core, route table, API responses, UI, metrics, quotas, database schema and deployment files use provider-neutral terms.
3. Required startup capabilities are exactly `planning.chat` and `transcription.speech_to_text`, with at least one valid enabled route each.
4. All other capabilities are optional. Missing optional routes never block startup; they appear in `/capabilities` as unavailable and the UI presents a clear unavailable state.
5. A remote route has a secret reference, not a secret value. Secret values are encrypted or mounted privately, never returned to clients and never logged.
6. An asynchronous submission is route-affine. Once a provider job may exist remotely, retries poll the original route snapshot and never submit to a different route.
7. Revisions follow Section 2.6:
   - Local and self-hosted models are pinned to an immutable revision or SHA; `latest` and floating aliases are rejected.
   - Remote and hosted routes are pinned, or use `revision: provider-managed` with an explicit per-route opt-in and a structured warning.
8. A paid route declares its pricing, or declares `pricing.status: unknown` together with a hard cap. A zero-priced route explicitly declares `explicitly_free: true`; private self-hosted free routes are allowed.
9. CI uses fake adapters and blocks real network access for normal tests. Real provider contracts remain manually authorized and configuration-driven.
10. No GPU is required for the baseline deployment.

### 0.3 Path notation

Paths in the audit tables are files that were actually found in the repository. Paths prefixed **NEW (proposed)** are implementation targets, not claims about files that already exist. Historical migrations are immutable records; new migrations must never add provider-specific schema names.

### 0.4 Compatibility posture

The legacy bridge depends on one question: **do live production users or data exist?** The default assumption is **no**.

**Path B — clean cutover (default: no live users).**

- One release. The registry file and secret references replace the vendor settings in a single change.
- The Tier 1 migrations rewrite existing development and test rows once (Task 18). Code reads only the new fields.
- Vendor environment variables are deleted from settings, Compose files and examples. The deployment guide lists them so operators can remove them.
- Take a database backup before the Tier 1 migrations. Rollback means restoring it and redeploying the previous images.

**Path A — bridged migration (only if live users or data exist).**

- **Release N:** registry plus legacy translator (Task 6L). Old variables and old JSONB payload keys still work, with deprecation warnings.
- **Release N+1:** production switches to the registry. Old payload keys are still read alongside the new ones (dual-read).
- **Release N+2:** the translator, old settings and dual-read are removed.
- **Ordering constraint:** Tasks 11–12 must land before Task 19 removes the vendor wiring, so live users keep captions, voice emotion and remote embeddings. Task 17 already comes before Task 19 in both paths.

| | Path B (default) | Path A |
|---|---|---|
| Releases | 1 | 3 (N, N+1, N+2) |
| Work this path drops from the plan | Task 6L translator and converter; old-versus-new parity tests; dual-read of old payload keys; staged enum and constraint rollout; deprecation warnings; Path A ordering constraints; mapping of old quota setting names | Nothing. It adds the items on the left and removes them again in Release N+2 |
| What it finally removes from the codebase | Vendor settings, wiring, secrets and old payload keys, all in one release | The same, plus Task 6L, in Release N+2 |

Both paths end in the same final state.

---

## 1. Audit findings

### 1.1 Repository and existing architecture

The repository is a pnpm/Next.js frontend plus FastAPI/Python API and workers, with Supabase migrations and tests:

- API code: `apps/api/app/`
- API tests: `apps/api/tests/`
- Web UI: `apps/web/`
- Python/Node workers: `apps/worker/`, `apps/api/app/workers/`
- Database migrations/tests: `supabase/migrations/`, `supabase/tests/`
- Deployment: `infra/docker-compose.gpu.yml`, `infra/docker-compose.remote-ai.yml`, related env examples and deployment scripts
- Shared API contract: `packages/shared/openapi.json`, `packages/shared/src/generated/api.ts`
- Existing docs convention: `docs/*.md`, `docs/security/`, `docs/ops/`

The worktree already contains extensive unrelated modified and untracked files. Implementers must stage only files belonging to each change and must not reset or overwrite unrelated work.

### 1.2 Vendor coupling audit

| Actual file | Finding | Planned replacement |
|---|---|---|
| `apps/api/app/services/model_gateway_factory.py` | `OPENROUTER_BASE_URL`, `GROQ_BASE_URL`, `_HTTP_ADAPTERS = {"azure", "openai_compatible", "openrouter", "groq"}`, vendor-key construction and `_legacy_routes()` create OpenRouter planning and Groq transcription routes. | Build adapters from registry records `{id, type, base_url, auth, secret_ref}` (Task 6). Path A only: a one-release translator (Task 6L). Endpoint host comes from the configured URL, not a provider name. |
| `apps/api/app/services/model_adapters.py` | `OpenAICompatibleAdapter` is reusable, but `JinaEmbeddingsAdapter` and `JINA_BASE_URL` hard-code a provider-specific request shape. `AzureOpenAIAdapter` uses Azure-specific transport, which is acceptable only inside the Azure adapter implementation. | Keep protocol adapters under an adapter boundary. Delete the dedicated Jina adapter in Task 19 and add a closed `embeddings_input_style` option in Task 12. |
| `apps/api/app/services/model_domain_adapters.py` | Contains `RunwayVideoGenerationAdapter`, Jina registration, OpenRouter ZDR registration, Google Vision registration, pyannoteAI registration, LALAL registration and vendor-named route ids/provider strings. | Capability routes come from the registry. The object, diarization and stem integrations are ported to templates (Task 17), and their vendor registrations are deleted after that (Task 19). Local runners remain behind a neutral local adapter (Task 14). |
| `apps/api/app/services/model_gateway.py` | Core is mostly neutral but uses coarse capability families and old operation names (`vision.prompt`, `audio.prompt`, `generation.video_submit`, etc.). Comments contain provider examples. | Preserve the gateway engine; add the fine-grained capability taxonomy and compatibility aliases. Move provider examples into docs. |
| `apps/api/app/services/model_gateway_factory.py`, `apps/api/app/main.py`, `apps/api/app/workers/visual_job.py`, `apps/api/app/workers/matte_job.py` | API and workers construct gateways from environment-specific vendor switches. Workers construct their own gateway in separate processes. | One registry/runtime builder used by API and workers; registry version and route fingerprints keep them aligned. |
| `apps/api/app/services/model_provider_bridges.py` | `GatewayEditPlannerProvider` subclasses the direct HTTP planner and carries endpoint/key compatibility details. | Make the bridge accept only a gateway and typed planning request. |
| `apps/api/app/services/edit_planner.py` | Direct HTTP planner remains and calls `check_quota(provider="openrouter", category="tokens")`. | Gateway-only planning. Quota is keyed by Motionly feature/category, not route provider (Task 10, Decision B-1). |
| `apps/api/app/services/transcription_provider.py`, `apps/api/app/main.py` | Non-production fallback uses `GROQ_TRANSCRIPTION_ENDPOINT` and Groq credentials directly. | Gateway-only transcription with fake route in CI/dev. |
| `apps/api/app/services/remote_visual.py` | Caption, object and image-embedding enrichers call vendor-specific route registration helpers and vendor-specific route ids. | Enrichers request `vision.caption`, `vision.object_detection` and `embeddings.image`; batch limits and mappings belong to route metadata. |
| `apps/api/app/services/google_vision.py` | Provider-specific object-localization HTTP adapter, `GOOGLE_VISION_*`, label aliases and vertex-to-box conversion. | Port to a `generic_http` template with conformance fixtures (Task 17, Tier 1). Archive the file under a Git tag, then delete it after conformance passes (Task 19). |
| `apps/api/app/services/pyannoteai_diarization.py` | Provider-specific upload reservation, signed upload, async submit/poll, 20-second minimum billing and seconds-to-milliseconds parsing. | Port to an `async_job` template with conformance fixtures (Task 17, Tier 1). The neutral wrapper that falls back to a degraded result moves to a neutral module. Archive the file under a Git tag, then delete it after conformance passes (Task 19). |
| `apps/api/app/services/lalal_stems.py` | Provider-specific FLAC conversion, upload, split submit, POST polling, artifact download limits/host checks and cleanup. | Port to an `async_job` template with conformance fixtures (Task 17, Tier 1). Archive the file under a Git tag, then delete it after conformance passes (Task 19). Local CPU Demucs stays available as a `local` route (Task 14). |
| `apps/api/app/services/remote_audio_emotion.py` | OpenRouter-specific class name and route setup although prompt/schema behavior is a generic audio chat strategy. | Move to a Motionly-owned audio-emotion strategy using any route that supports `audio.emotion`. |
| `apps/api/app/services/scale.py` | `RunwayGen3Provider`, `FixtureRunwayProvider`, `RunwayResult`, `runway_task_id`, `runway_gateway_route`, Runway cost/quotas/errors, provider-specific download names. | Generic video-generation operation records, neutral `generated` asset kind and `provider_operation_id`/`provider_route_id`/`provider_model_revision`. |
| `apps/api/app/api/scale.py` | Endpoint function `generate_runway_broll`, Runway premium copy, `runway_not_configured`. The URL `/projects/{project_id}/phase5/generated-broll` is already neutral. | Rename the operation, use neutral copy and return `capability_unavailable`. |
| `apps/api/app/services/job_engine.py` | Passes `runway_provider` and `runway_gateway_route` through job execution. | Pass a generic inference runtime/operation service. |
| `apps/api/app/services/billing.py` | `PROVIDER_QUOTAS` keys `runway` and `openrouter`; `PlanFeatureQuotaGuard` counts by `route.provider`, so changing providers can reset a workspace's effective feature quota. | Keep the guard but key it by Motionly feature/category (Decision B-1). Provider label remains telemetry metadata only. |
| `apps/api/app/services/model_gateway_factory.py`, `apps/api/app/core/config.py`, `apps/api/app/core/production.py` | Vendor-specific route defaults, provider allowlists, required keys, pricing checks and pinned revision maps. Production currently hard-requires Runway generation in addition to planning and transcription. | Validate enabled routes by capability: planning and STT required; all others optional. Validate auth, revision policy, pricing and GPU suitability generically (Section 2.6). |
| `apps/api/app/api/health.py` | `_probe_providers()` hard-codes OpenRouter, Groq and Runway URLs/headers. | Probe each configured adapter's declared health request and expose route/circuit health without provider names. |
| `apps/api/app/core/logging.py`, `apps/worker/src/log-redaction.mjs` | Prefix-specific redaction for Groq and OpenRouter keys and a generic `sk-` rule. | Register resolved secret values with a generic redactor and retain only protocol-neutral token patterns. |
| `apps/api/scripts/audit_local_capabilities.py` | Hard-coded vendor key names in local capability auditing. | Audit secret references and route declarations from the registry. |
| `apps/api/app/services/ai_features.py` | Maps feature switches to vendor credentials and provider names. | Replace with a registry-derived capability report. |
| `apps/api/app/core/config.py` | Settings include `GROQ_*`, `OPENROUTER_*`, `RUNWAY_*`, `JINA_*`, `GOOGLE_VISION_*`, `PYANNOTEAI_*`, `LALAL_*`, Azure-specific keys and vendor default model ids. | Keep only generic registry/secrets/budget settings (Task 26). |
| `apps/api/app/core/production.py` | Requires `GROQ_API_KEY`, `OPENROUTER_API_KEY`, Runway key/cost and vendor-specific optional credentials; checks vendor-specific provider enum values. | Replace with one capability-based registry validator. |
| `infra/docker-compose.gpu.yml` | Mounts `groq_api_key`, `openrouter_api_key`, `runway_api_key`, `azure_openai_api_key` and environment `_FILE` variables. | Mount one registry file and one generic secrets bundle (Tier 3 adds the KEK). |
| `infra/docker-compose.remote-ai.yml` | Remote mode defaults and prices name Google Vision, OpenRouter, Jina, pyannoteAI and LALAL; mounts their secret files. | Keep topology and CPU/GPU placement only. Routes and prices move into registry config. |
| `.github/workflows/live-provider-contracts.yml` | Passes `OPENROUTER_API_KEY`, `GROQ_API_KEY` and `RUNWAY_API_KEY` directly into a vendor-specific test. | Run registry-selected contract scenarios using a generic secrets bundle and explicit manual authorization. |
| `scripts/deploy/apply_release.sh` | Fetches provider-specific secret-store names at lines 282–284. | Fetch the generic registry and secrets bundle. |
| `scripts/release/write_compose_test_env.py`, `setup-local-env.sh`, `setup-local-env.ps1`, `.env.example`, `apps/api/.env.example`, `prod.env.example`, `infra/production-compose.env.example`, `infra/compose-host/host.env.example` | Document or generate vendor-specific variables and secret files. (`apps/worker/.env.example` was re-checked and has none.) | Replace with generic registry and secrets settings (Task 26). |
| `apps/web/components/editor/ScalePanel.tsx` | UI state, section title and toast say `Runway Gen-3 B-roll`. | Rename to Generated B-roll (Task 18) and gate it on `video.generation.submit` (Task 9B). |
| `apps/web/app/privacy/page.tsx` | Hard-coded processor sections for Google Cloud Vision, OpenRouter, Jina AI, pyannoteAI and LALAL.AI. | Render configured adapter data-processor metadata (Task 9A). |
| `apps/api/app/schemas/broll.py`, `apps/api/app/repositories/broll.py`, `packages/shared/src/index.ts`, `packages/shared/src/generated/api.ts`, `packages/shared/openapi.json` | `stock_provider` includes/queries `runway`; generated API operation is Runway-named. | Add neutral `generated` and regenerate contracts. |
| `supabase/migrations/202606210003_phase_5_scale.sql` | Adds `runway` to `public.stock_provider`. | Add `generated` in a forward migration and stop writing `runway`; the old enum value stays physically but is no longer used. |
| `phase5_resources.payload` JSONB | Generated B-roll writes `runway_task_id`, route/model metadata and submit state. | Introduce `inference_operations` with `provider_operation_id`, `provider_route_id`, `provider_model_revision`; one-time rewrite (Path B) or one-release dual-read (Path A). |
| `stock_assets.provider_asset_id`, `cost_attribution_events.metadata.task_id` | Store provider task id and vendor label indirectly. | Store neutral operation id/route metadata; historical cost rows stay as recorded. |
| `apps/api/tests/fixtures/providers/runway_succeeded.json`, Runway tests in `apps/api/tests/` and vendor contract workflow | Tests assert vendor-specific transport and credentials. | Port to the fake async adapter. |
| `docs/generated/motionly_technical_documentation.md`, `/workspaces/Motionly-claude/DEPLOYMENT-GUIDE.md` (Git root), `docs/security/data-handling.md`, `docs/security/secrets.md`, `docs/ops/compose-vm-deployment.md`, `docs/ops/supply-chain.md` | Vendor-specific operational and privacy documentation. | Rewrite operator guidance to registry concepts; vendor names may remain only as documented adapter examples. |

### 1.3 Specific requested audit answers

**SDK calls.** No Anthropic or OpenAI SDK call was found. `apps/api/pyproject.toml` has no such SDK dependency. Current outbound inference calls are raw `httpx` implementations. Strings such as `openai/gpt-4.1-mini` and `openai/clip-vit-base-patch32` are model ids or protocol naming, not SDK calls; they must still be moved out of core defaults.

**Production validation.** The main coupling is `apps/api/app/core/production.py`: it validates vendor-specific provider values and keys, requires Runway generation, and includes vendor-specific pricing and revision checks. This is replaced with route/capability validation.

**Health checks.** `apps/api/app/api/health.py::_probe_providers` is vendor-specific. The replacement uses each adapter's declared health specification and reports route/circuit state through capability-neutral names.

**Pricing.** Pricing is spread across `apps/api/app/core/config.py`, `model_gateway_factory.py`, `model_domain_adapters.py`, `billing.py`, `apps/api/app/services/scale.py` and `infra/docker-compose.remote-ai.yml`. It becomes route-level pricing, with feature quota categories separate from provider identity.

**Runway persisted state.** There is no `runway_task_id` SQL column. It currently appears in `phase5_resources.payload` JSONB in `apps/api/app/services/scale.py`. The provider label also appears in the `public.stock_provider` enum, `stock_assets`/`broll_slots`, cost-attribution provider values and generated UI/API contracts. The plan explicitly migrates all of these.

### 1.4 Existing gateway inventory and reuse decision

| Existing component | Actual location | Decision |
|---|---|---|
| Typed requests/outputs (`PlanningRequest`, `TranscriptionRequest`, `VisionRequest`, `EmbeddingsRequest`, generation, matte, stems, diarization, object detection) | `apps/api/app/services/model_gateway.py` | Keep and split/rename into the fine-grained taxonomy; re-export old names while callers move. |
| `ModelRoute` with id, capability, adapter, model, revision, priority, pricing, `explicitly_free`, enabled state, endpoint host and budget feature | `model_gateway.py` | Keep as the execution engine model; extend with timeout, GPU/CPU requirement, limits, strategy, scope, revision opt-in, pricing status, hard cap and model metadata. |
| `ModelGateway.invoke` route filtering and priority fallback | `model_gateway.py` | Keep. Change operation mapping and make post-submit async failures non-fallback. |
| `required_route_id` and `required_model_revision` | `model_gateway.py` | Keep and use for status calls; add a persisted immutable route snapshot for jobs. |
| In-memory and Redis budget ledgers | `model_gateway.py` | Keep. Add operation/test scopes and per-route hard-cap accounting. |
| In-memory and Redis circuit stores | `model_gateway.py` | Keep. Add read-only state/last-transition methods for admin health. |
| `ProviderErrorInfo` and provider status classification | `apps/api/app/services/provider_resilience.py` | Keep; add `sent`/`commit_reached` to distinguish safe fallback from submit uncertainty. |
| `PlanFeatureQuotaGuard` and `FEATURE_QUOTAS` | `apps/api/app/services/billing.py` | Keep; remove vendor-keyed quota categories and count by Motionly feature/category. |
| `OperationsGatewayTelemetry` and `provider_slo_events` fields | `model_gateway.py`, migration `202607140010_provider_neutral_model_gateway.sql` | Keep; use route id/model revision/capability as stable labels and never store prompts/media/secrets. |
| `OpenAICompatibleAdapter` and `AzureOpenAIAdapter` | `apps/api/app/services/model_adapters.py` | Keep behind registry adapter types; the former covers many compatible endpoints. |
| Local adapters for visual manifest, audio intelligence, CLIP, matte and stems | `apps/api/app/services/model_domain_adapters.py` | Keep behind a neutral local adapter; remove vendor/model defaults from core settings. |
| `route_catalog_fingerprint` and embedding cache-version logic | `model_gateway.py`, `model_domain_adapters.py` | Keep; route/model revision changes must invalidate caches. |
| `ai_feature_report`, `gpu_worker_switches` and `degraded_capabilities` | `apps/api/app/services/ai_features.py`, `remote_visual.py` | Keep the behavior, replace implementation with registry-derived capability state. |
| `_FILE` secret loading and media/ML subprocess environment allowlists | `apps/api/app/core/config.py`, `apps/api/app/services/subprocess_env.py`, `apps/worker/src/subprocess-env.mjs` | Generalize secret loading; preserve allowlists so generic secrets never reach ffmpeg/Chromium/Demucs unnecessarily. |
| `require_admin` | `apps/api/app/api/admin.py` | Reuse for registry, secret, test-route and health APIs. |
| `cryptography>=48.0.1,<49.0` | `apps/api/pyproject.toml` | Use existing dependency for AES-GCM; no encryption dependency is needed. |
| `FakeAdapter` test doubles | `apps/api/tests/test_model_gateway.py`, `apps/api/tests/test_model_gateway_feature_budgets.py` | Promote into a shared no-network fake adapter/server. |

**Known gaps to implement:** encrypted DB secret store; declarative registry; admin API/UI; generic HTTP mapping; generic async submit/poll/artifact adapter; route-level timeout/health; per-route test action; license metadata; neutral required/optional startup validation; outbound GPU-agent design; and a final neutrality scanner.

---

## 2. Target design

### 2.1 Capability taxonomy and typed Motionly contracts

Capability ids are stable Motionly API concepts. Adapter details never leak into these schemas.

| Capability | Motionly-owned request | Motionly-owned response | Execution notes |
|---|---|---|---|
| `planning.chat` | `PlanningRequest(messages, response_format, temperature, max_output_units)` | `PlanningOutput(content, raw_response, usage)` | Required at startup. |
| `transcription.speech_to_text` | `TranscriptionRequest(audio, language, prompt, response_format)` | `TranscriptionOutput(text, language, confidence, words[])` | Required at startup; word timestamps remain in milliseconds. |
| `vision.caption` | `VisionCaptionRequest(images, prompt, max_output_units, response_format)` | `VisionCaptionOutput(captions[], raw_response)` | Optional; may use a chat/vision strategy. |
| `vision.object_detection` | `ObjectDetectionRequest(images, max_results)` | `ObjectDetectionOutput(detections-per-image[], raw_response)` | Boxes normalized to `[0,1]`. |
| `embeddings.text` | `TextEmbeddingsRequest(inputs, dimensions?)` | `EmbeddingsOutput(vectors[])` | Text and image routes must declare the same `embedding_space` when consumers compare them. |
| `embeddings.image` | `ImageEmbeddingsRequest(images, source_hints?)` | `EmbeddingsOutput(vectors[])` | Same vector-space rule. |
| `audio.diarization` | `DiarizationRequest(audio, duration_s)` | `DiarizationOutput(segments[], speaker_count, raw_response)` | May be a blocking facade over an async provider job. |
| `audio.emotion` | `AudioEmotionRequest(audio, window_ms, chunk_seconds, response_format)` | `AudioEmotionOutput(windows[], raw_response)` | Optional; generic audio-chat strategy. |
| `audio.stem_separation` | `StemSeparationRequest(source, requested_stems, duration_s?)` | `StemSeparationOutput(stems: name → artifact)` | Must support local CPU and remote async. |
| `image.generation` | `ImageGenerationRequest(prompt, input_images, size, count, response_format)` | `GenerationOutput(artifacts[], operation_id?)` | Optional; no current product caller is required for cutover. |
| `video.generation.submit` | `VideoGenerationSubmitRequest(prompt, conditioning_image, duration_s, ratio, idempotency_key)` | `OperationHandle(provider_operation_id, route_id, model_revision)` | Optional; submit is never retried on another route after send uncertainty. |
| `video.generation.status` | `VideoGenerationStatusRequest(provider_operation_id, original_route_id, original_revision)` | `OperationStatus(state, artifacts, error, usage)` | Poll remains bound to original route snapshot. |
| `matte.segmentation` | `MatteSegmentationRequest(source, output, metadata, max_dimension)` | `MatteSegmentationOutput(fps, width, height, frame_count)` | Local CPU route is valid. |
| `audio.sound_classification` | Existing sound-audio request normalized into a typed contract | Labels/scores and degradation status | Added to preserve current CPU YAMNet behavior. |

`vision.video_manifest` and `audio.intelligence` remain orchestration/pipeline concepts. They call the fine-grained capabilities rather than becoming provider routes themselves.

Old `ModelOperation` values remain aliases only while callers move (inside Tier 1; Path A: until Release N+2). New telemetry and configuration use the ids above.

### 2.2 Adapter registry

The registry is declarative and has no one-environment-variable-per-vendor design. Tier 1 loads it from a JSON file (Task 4). An adapter record:

```json
{
  "id": "operator-endpoint-1",
  "type": "openai_compatible",
  "base_url": "https://example.invalid/v1",
  "network": "public",
  "auth": { "type": "bearer", "secret_ref": "bundle:operator-endpoint-1" },
  "timeout_s": 60,
  "health": { "method": "GET", "path": "/models" },
  "extra_headers": {},
  "extra_body": {},
  "data_processor": {
    "display_name": "Operator-configured endpoint",
    "purpose": "Inference request processing",
    "data_categories": ["audio", "images", "text"],
    "policy_url": "https://example.invalid/policy"
  }
}
```

Supported adapter types, with the tier that delivers them:

1. `openai_compatible` (Tier 1): chat, vision chat, audio input, transcription, embeddings and image generation where the endpoint supports the relevant Motionly contract. It covers compatible hosted and self-hosted endpoints, including OpenRouter, Groq, Together, vLLM, Ollama, LM Studio and llama.cpp without core code branches.
2. `azure_openai` (Tier 1, existing `AzureOpenAIAdapter`): deployment-scoped URLs and Azure auth; implementation-specific behavior is isolated under the adapter.
3. `local` (Tier 1, Task 14): an in-process or local-worker runner for CPU/GPU models, selected by route metadata.
4. `fake` (Tier 1, Task 1): deterministic CI/dev adapter, rejected in production.
5. `generic_http` (Tier 1 slice in Task 15; Tier 3 extensions in Task 28): HTTP/JSON/multipart request and response mapping, limited to the grammar in Section 2.10.
6. `async_job` (Tier 1 slice in Task 16B; Tier 3 extensions in Task 28): declarative submit, poll, result/artifact and cleanup steps, limited to the grammar in Section 2.10.
7. `huggingface_endpoint` (Tier 3, Task 24): TGI/TEI/pipeline modes with typed response mappings.
8. `aws_bedrock` (Tier 3, Task 25): IAM credential chain and SigV4; model-family request mapping is adapter/config data.
9. `agent_pool`: reserved design type for a later outbound-only GPU agent; not built in this plan.

Auth types:

- Tier 1 (Task 5): `none` for private endpoints only; `bearer`; `custom_header` with a configured header name; Azure `api-key`; OAuth2 client credentials with token URL, client secret reference and scope.
- Tier 3 (Task 25): AWS SigV4 with IAM credential chain.

Mapping templates exist only for `generic_http` and `async_job`, and only within Section 2.10. A template cannot alter the adapter host, access environment variables or secrets, execute code or make an unapproved second network request.

### 2.3 Route table

A route record contains:

- `id`
- `capability`
- `adapter_id`
- `model_id` and an allowlist model reference
- `model_revision`: an immutable revision or SHA; for remote routes only, the literal `provider-managed` is also allowed (Section 2.6)
- `allow_provider_managed_revision`: boolean, default `false`; required to be `true` for `provider-managed`
- `priority` and explicit fallback order
- `strategy`: `native`, `chat_prompt`, `generic_mapping`, `async_job`, or `local`
- `pricing`: `status: priced|free|unknown`. `priced` carries unit, input/output price, per-request/per-unit price and minimum billable units. `free` requires `explicitly_free: true`. `unknown` requires a hard cap.
- `budget`: `feature`, optional `monthly_usd`, optional `hard_cap {max_requests_per_month, max_units_per_month}` (required when `pricing.status` is `unknown`)
- `timeout_s`
- `requires_gpu` / `compute_requirement: cpu|gpu|remote|any`
- request limits: batch size, bytes, duration and output units
- `enabled`
- `scope: global|workspace:<id>` (workspace scope disabled initially)
- `data_processor` reference for public privacy metadata
- `source: file|database`

The route's provider label is metadata for operators and telemetry only. Capability availability, quota, fallback and UI behavior never branch on that label.

### 2.4 Config file, database and admin management

**Tier split:** Tier 1 uses the file only, with `file:` and `bundle:` secret references. The database registry, `db:` secrets and admin editing are Tier 3 (Tasks 20–23).

Both configuration sources are supported once Tier 3 lands:

- **File/seed:** `AI_REGISTRY_FILE` points at a JSON registry used for bootstrap, reproducible deployments and CI. File entries are read-only in the UI and can be replaced by a deployment rollout.
- **Database/admin:** `ai_adapters`, `ai_models`, `ai_routes` and `ai_secrets` are edited through admin-only API/UI. A registry version counter triggers atomic runtime reload. File and database ids must not collide.

Recommended scope is **global operator keys now**. The database carries scope fields for future true BYOK, but workspace-scoped routes are rejected while the feature flag is false. When workspace BYOK is later enabled, it should require public-host restrictions, explicit workspace billing/privacy policy and route isolation.

Secret rules:

- Secret refs are names such as `file:route-secret`, `bundle:operator-secrets` (Tier 1) or `db:route-secret` (Tier 3); values never appear in route responses.
- DB secrets use AES-256-GCM with a deployment KEK from `AI_SECRETS_KEK_FILE`; AAD binds secret name, scope and key version.
- Key rotation supports current and previous KEK during a bounded rotation window; a re-encryption command rewrites ciphertexts.
- Secret metadata exposes name, scope, fingerprint and timestamps only.
- Resolved values are registered with the generic redactor before use.
- Secrets are never passed into ffmpeg, Chromium or unrelated media subprocesses. Existing allowlists in `apps/api/app/services/subprocess_env.py` and `apps/worker/src/subprocess-env.mjs` remain mandatory.

### 2.5 Async operation model

**Tier split:** Tier 1 builds the operation record and gateway rules (Task 16A) and the `async_job` slice (Task 16B), which runs in blocking mode for diarization and stems (Task 17). Delayed queue polling for non-blocking jobs is Tier 3 (Task 28). Until then, a Tier 1 video route can only use blocking mode (Task 18).

Add an `inference_operations` record before a remote submit. It records an idempotency key, capability, original route id/revision, immutable route snapshot, provider operation id when known, and state.

State transitions:

`reserved → submitting → submitted/running → succeeded|failed|cancelled`

An after-send timeout or transport failure becomes `submit_unknown`, not a fallback opportunity. It is reconciled by polling the original route. A status poll is never re-routed because a route was disabled, reprioritized or edited after submit. Disabled routes remain available for status calls until all referenced operations are terminal.

A submit is retried only when failure is proven before the remote request could have been sent. Poll calls are not charged as a new submit. Blocking consumers such as diarization/stems may wait through a bounded poll loop, but operation state is persisted so worker retries resume rather than resubmit. Video generation uses delayed queue polling.

Persisted neutral fields are:

- `provider_operation_id`
- `provider_route_id`
- `provider_model_revision`
- `provider_model`
- `route_snapshot`

The word `provider` here means an opaque external execution identity, not a hard-coded vendor field; user-facing and core routing logic uses route/capability terminology.

### 2.6 Startup validation and capability endpoint

Each rule produces either an **error** (the route is invalid) or a **warning** (the route stays active; the warning is logged with the route id and reported to admins). If a required capability has no valid route, startup fails. An invalid optional route is dropped, and its capability is reported `unavailable`.

**Route locality.** A route is **self-hosted** when its adapter type is `local` or `agent_pool`, or its adapter declares `network: private`. Every other route is **remote/hosted**.

| Rule | Self-hosted / local routes | Remote / hosted routes |
|---|---|---|
| Required capabilities | `planning.chat` and `transcription.speech_to_text` each need at least one valid enabled route, or startup fails | Same |
| Revision | An immutable pin is required: SHA-256, `==version`, a date, `vN` or a commit hash. `latest`, floating aliases and `provider-managed` are errors | A pinned revision; or `revision: provider-managed` with `allow_provider_managed_revision: true`, which logs warning `revision_provider_managed`. These are errors: `provider-managed` without the flag, `latest`, other floating aliases, and a revision equal to the model id |
| Licence and commercial use | Required; missing is an error. A non-commercial licence is a warning, or an error when `AI_BLOCK_NONCOMMERCIAL=true` | Optional; missing is a warning |
| Data-processor metadata | Not required | Missing is a warning in dev and an error in production (`APP_ENV=production`) |
| Auth | `auth: none` is allowed | Auth with a secret reference is required |
| Pricing | Priced, or `explicitly_free: true` | Priced; or `explicitly_free: true`; or `pricing.status: unknown` with a hard cap. A zero price without `explicitly_free` is an error |
| Free planning route | Allowed | Error in production |
| Fake adapter | Error in production | Error in production |
| GPU requirement | `requires_gpu` routes are dropped when `GPU_WORKERS_ENABLED=false` | Not applicable |

**Current code to change.** `ModelRoute.__post_init__` raises for any revision that fails `is_immutable_model_revision`, and that function explicitly rejects `provider-managed` (`apps/api/app/services/model_gateway.py`, around lines 607 and 1854). Task 4 moves the revision rule into the registry validator, which knows route locality. The engine then accepts `provider-managed` only on routes that carry the opt-in flag.

**Cache identity with `provider-managed`.** The provider can change the model without Motionly noticing. Cache keys and `route_catalog_fingerprint` contain the literal `provider-managed`. Telemetry records the model identifier the provider returns in its response when the protocol includes one (OpenAI-compatible responses carry a `model` field). The warning recommends pinning embeddings routes, because cached vectors from a changed model would mix vector spaces without any visible error.

**Hard cap for unknown pricing.** A paid route with `pricing.status: unknown` is valid only if `budget.hard_cap` sets `max_requests_per_month`, `max_units_per_month` or both (each greater than 0).

- With no price, the cap is counted in requests or billing units, not dollars.
- The existing budget ledgers (`InMemoryBudgetLedger` and `RedisBudgetLedger` in `model_gateway.py`) reserve and settle plain numbers, so they hold a per-route monthly scope.
- When the cap is reached, the route is skipped with code `route_hard_cap_exhausted` and fallback continues.
- Telemetry records `actual_cost_usd: 0` with `cost_unknown: true`, so dashboards do not show the route as free.
- Workspace dollar budgets cannot see this spend. Production logs a warning for each such route at startup.

`GET /capabilities` returns capability id, availability, reason, async status, compute requirement and route count. It never returns base URLs, model secrets or sensitive provider metadata. Detailed route, warning and circuit information is admin-only: in Tier 1 through the authenticated `/health/dependencies` report, and in Tier 3 through the admin API.

### 2.7 Operator tooling

**Tier split:** Tier 1 operator tooling is limited to three things:

- the registry validator CLI (Task 4)
- `GET /capabilities`
- validation warnings in the authenticated `/health/dependencies` report, which the existing admin ops page (`apps/web/app/(app)/admin/ops/page.tsx`) already fetches

Everything below is Tier 3 (Tasks 21–23).

Admin-only endpoints/UI provide:

- Add/edit/disable adapters, models and routes.
- Write-only secret creation/rotation; no secret read endpoint.
- `POST /admin/inference/routes/{route_id}/test` with a tiny typed sample, schema validation, latency, usage and cost. Async test submits require explicit `allow_paid=true` and use a separate operator budget scope. Tests of hard-capped routes count against the cap.
- `GET /admin/inference/routes/health` with circuit state, last success/failure, p50/p95 latency, error rate, fallback count and budget or hard-cap usage.
- Route validation before save and a dry-run registry import.
- Warning badges for `provider-managed` revisions, unknown pricing, missing remote licence metadata, missing data-processor metadata in dev, and non-commercial licences.

### 2.8 Later self-hosted/GPU agent design

An `openai_compatible` route with a private base URL already covers an Ollama/vLLM-style endpoint. For a separate operator GPU, design (do not build now) an outbound-only agent:

- GPU agent makes outbound HTTPS long-poll/heartbeat connections.
- No inbound ports on the GPU box.
- No cloud credentials, database credentials or provider keys on the GPU box.
- API issues a one-time enrollment token and rotating scoped agent token.
- Inputs are fetched from object storage using short-lived signed URLs; outputs are uploaded using signed URLs.
- Agent advertises allowlisted capability/model revisions and receives leased jobs.
- Agent routes are `agent_pool` entries and use the same typed contracts and operation table.

### 2.9 Model allowlist metadata

Every model entry records these fields:

- id and external model id
- revision or SHA (`provider-managed` is allowed for remote routes only, Section 2.6)
- licence (SPDX id or a proprietary marker) and commercial-use flag
- source type and source URL
- capability list
- minimum VRAM
- weights SHA-256
- verification timestamp

Requirements by locality:

- **Local and self-hosted models:** licence and commercial-use flag are required, a pinned revision is required, and a weights SHA-256 is required whenever Motionly downloads the weights. Today's `MATTE_CPU_MODEL_SHA256`, `YAMNET_TFLITE_MODEL_SHA256` and `VISUAL_CPU_FACE_MODEL_SHA256` settings already follow this pattern.
- **Remote/hosted models:** licence and commercial-use metadata are optional; missing values produce a warning.

Non-commercial licences produce a warning and an admin badge (an error with `AI_BLOCK_NONCOMMERCIAL=true`). Implementers must verify each local model's current licence from its authoritative source in Task 14. Licences must never be inferred from model names.

### 2.10 Adapter and mapping scope by tier

Tier 1 ships a **minimal mapping slice**. It contains only what object detection, diarization and stem separation need for parity (Task 17). Planning, transcription, image generation, captions, voice emotion and embeddings need no mapping: they use `openai_compatible` with the closed options below, or `azure_openai`, `local` or `fake`. Everything beyond the slice is Tier 3 (Task 28).

| Capability | `openai_compatible` alone | Needs `generic_http` | Needs `async_job` | Local option | Tier |
|---|---|---|---|---|---|
| `planning.chat` | Yes: `chat/completions` | No | No | Future private endpoint | 1 |
| `transcription.speech_to_text` | Yes: `audio/transcriptions` with `verbose_json` and word timestamps | Only for non-compatible APIs (Task 28) | No | Future private endpoint | 1 |
| `matte.segmentation` | No | No | No | MediaPipe on CPU | 1 (Task 14) |
| `audio.sound_classification` | No | No | No | YAMNet TFLite on CPU | 1 (Task 14) |
| `vision.object_detection` | No: no standard request | Yes: Tier 1 slice | No | No | 1 (Tasks 15, 17) |
| `audio.diarization` | No: no standard request | Sync APIs: Tier 1 slice | Async APIs: Tier 1 slice, blocking mode | GPU model; transcript heuristic fallback | 1 (Tasks 15, 16B, 17) |
| `audio.stem_separation` | No | No | Yes: Tier 1 slice, blocking mode | Demucs on CPU | 1 (Tasks 14, 16B, 17) |
| `video.generation.submit` / `.status` | No: no standard request | No | Yes: Tier 1 slice, blocking mode | No | 1 if the provider fits the slice; otherwise Tier 3 (Task 28) |
| `image.generation` | Yes: `images/generations` | No | No | No | Route type available from Tier 1; no product caller |
| `vision.caption` | Yes: chat with image parts and Motionly's prompt | Only for non-compatible APIs (Task 28) | No | GPU models only | 2 |
| `audio.emotion` | Yes: chat with `input_audio`; the route must use an audio-input model | No | No | GPU model only | 2 |
| `embeddings.text` | Yes: `/embeddings` | Only for other request shapes (Task 28) | No | CLIP, GPU registry only | 2 |
| `embeddings.image` | Yes, with `embeddings_input_style: typed_objects` | Only for other request shapes (Task 28) | No | CLIP, GPU registry only | 2 |

**Closed `openai_compatible` options.** These are data, not templates:

- `extra_headers`: static strings.
- `extra_body`: static JSON merged under the request fields, which always win. This is the existing behavior in `model_adapters.py`.
- `embeddings_input_style: openai|typed_objects` (Tier 2, Task 12).
- `timeout_s`.

`azure_openai` adds `api_version`.

**Tier 1 grammar slice (Tasks 15 and 16B).** This is the complete language in Tier 1; nothing else is accepted.

Value forms:

1. A JSON literal.
2. `{"$ref": "<path>"}` reads a value. The allowed roots are:
   - `input.*`: typed Motionly request fields, including `input.file` (the prepared media file) and `input.images`
   - `params.*`: route parameters
   - `vars.*`: values set by the `vars` initializer or captured by earlier steps
   - `response.*`: the current response only

   Path segments are `key`, `[n]`, `[*]` and `{vars.<name>}`, which looks up a key named by a variable (for example a status keyed by the job id).
3. `{"$fn": "<name>", "args": [...]}` calls one of the functions below.
4. `{"$map": {"over": "<path>", "each": <value>}}` maps over a list, bounded by the route's `limits.max_batch`. `item.*` refers to the current element.
5. Placeholders inside request paths only, for example `/jobs/{vars.job_id}`. Values are percent-encoded. Scheme, host and port can never be templated.
6. A `vars` block, evaluated once per call before the first step, for values that must stay the same across steps (for example one random media name used by both the upload reservation and the submit).

Allowed functions (the complete list):

| Function | Purpose |
|---|---|
| `base64(bytes)` | Base64-encode request bytes |
| `data_url(bytes)` | `data:<sniffed image MIME>;base64,…`; unknown formats are rejected |
| `uuid()` | Random UUID for idempotency keys |
| `concat(a, b, …)` | Join string literals and values; used only to build opaque identifiers such as `media://motionly-<uuid>` |
| `to_int(x)` | Integer conversion; failure is a malformed response |
| `to_float(x)` | Float conversion; failure is a malformed response |
| `to_ms(seconds)` | Seconds to integer milliseconds |
| `clamp01(x)` | Clamp to `[0, 1]` |
| `default(x, fallback)` | Use `fallback` when `x` is missing or null |

**`generic_http` mapping, Tier 1 slice (Task 15):** one JSON request per call, with an optional pre-request input transform.

- `request`: method, path and JSON `body`.
- `items`: a path to the result list. `aligned_to_input: true` requires one result per input item, and a length mismatch is a malformed response.
- Per-field paths for the capability's output fields (for example label, score and box).
- `box_format`: `vertices_norm` (an axis-aligned box from normalized vertices; a missing coordinate counts as 0) or `xywh_norm`.
- `item_error`: a path to a per-item error, plus `item_error_codes`, which maps provider codes to `rejected` (not billed, no fallback) or `unavailable` (retryable, not billed). If every item fails, the call raises that class. If only some fail, those items return empty results and a warning is logged.
- `error_map`: maps an HTTP status to one Motionly error class (`rejected`, `unavailable`, `auth`, `quota`, `malformed`).
- `usage`: `count_items` or `count_ok_items`.

**`async_job` mapping, Tier 1 slice (Task 16B), blocking mode only:**

- `steps`: an ordered list of requests. Each step has:
  - `capture`: values to read into `vars`;
  - `require`: for example that a captured URL is HTTPS;
  - `body`: JSON, or `input.file` for a raw upload;
  - `auth`: `adapter` (default) or `none`. With `none`, the request goes to the exact URL captured from an earlier response, which must pass `allowed_hosts` and the SSRF policy (Task 15); it is used for pre-signed upload links.
- `commit: true` on the step after which the provider may charge. No fallback happens after a committed step.
- `poll`: a GET or POST request; a `status` path; a `status_map` from provider values to `running`, `succeeded`, `failed` or `retryable_failed`, with `billable` and `fallback` flags; `tolerate_retryable` (keep polling on transient errors until the deadline); `interval_s`, `backoff` and `max_wait_s`.
- `result`: a mapping, as for `generic_http`.
- `artifacts`: `items`, `name`, `url`, `name_map`, `required`, `max_bytes`, `allowed_hosts`, `force_https`, and `auth: none`.
- `cleanup`: best-effort requests that run after both success and failure; failures are logged and never change the result.

**Route options used by the slice:**

- `input_transform.audio`: audio-only rendition (codec, sample rate, channels), produced with ffmpeg through `media_tool_env()` from `apps/api/app/services/subprocess_env.py`.
- `upload_filename`: the neutral filename sent with uploads.
- `label_aliases`: data for the object-detection normalizer.

**Motionly normalizers (code, not template functions):**

- Object detection: lowercase labels, collapse whitespace, cut to 80 characters, apply `label_aliases`, clamp scores and boxes, sort by score, keep the top N.
- Diarization: sort segments by start and end.
- Stems: map artifact names to `vocals` and `no_vocals`.

**Not allowed (any tier):**

- Conditionals.
- Loops other than `$map`.
- String building other than `concat` and path placeholders.
- Reading environment variables or secrets. Auth is applied by the adapter, never by a template.
- Changing the host, or contacting a second host other than a captured URL that passes `allowed_hosts` and the SSRF policy.

**Tier 3 extensions (Task 28), and nothing else:**

- Multipart request bodies.
- Additional box formats: `xyxy_norm` and `xyxy_px`.
- `usage` taken from a response path.
- Non-blocking async mode with delayed queue polling (needed for long video jobs).
- Typed mappings for synchronous transcription, captions and embeddings on APIs that are not OpenAI-compatible.

Any other extension requires a plan change.

---

## 3. No-GPU reality matrix

The baseline uses remote routes where the current packaged CPU path is insufficient and local CPU routes where they already exist.

| Capability | Remote-only/CPU execution today | Cheapest viable route type | Graceful no-route behavior | GPU or remote required? |
|---|---|---|---|---|
| `planning.chat` | Remote chat API; a private CPU llama.cpp-compatible endpoint can be added later | `openai_compatible` | Startup fails because required | Yes in the current packaged baseline; private CPU endpoint can remove the remote dependency later |
| `transcription.speech_to_text` | Remote multipart STT route; current non-GPU topology already expects remote transcription | `openai_compatible` | Startup fails because required | Yes in current baseline; CPU local inference is a future adapter route |
| `vision.caption` | Remote vision/chat route | `openai_compatible` or `generic_http` | Visual manifest marks captions unavailable/degraded; editing continues with transcript/CPU signals | Remote or a future CPU/GPU local model |
| `vision.object_detection` | Any remote detection API through a `generic_http` template (Tier 1) | `generic_http` | Empty detections; visual analysis lists `objects: unavailable:not_configured` in `degraded_capabilities` and editing continues | Remote today; a CPU route could be added later |
| `embeddings.text` | Remote embeddings or local CPU CLIP if the worker image is configured for it | Local CPU route is free; otherwise `openai_compatible` | B-roll ranking falls back to lexical matching | No if CPU CLIP is installed; otherwise remote |
| `embeddings.image` | Remote image embeddings or local CPU CLIP | Same embedding-space route as text | Keyframe/stock vector enrichment is skipped | No if CPU CLIP is installed; otherwise remote |
| `audio.diarization` | Any remote diarization API through a `generic_http` or `async_job` template (Tier 1); the local pyannote path is not in the CPU-only image | `async_job` (blocking) or `generic_http` | Heuristic speaker labels from the transcript (`heuristic_single_speaker_v1`), marked degraded | Remote or a future CPU local model |
| `audio.emotion` | Remote audio-chat route; local wav2vec2 remains a GPU/local route unless separately packaged for CPU | `openai_compatible` with audio input | Empty emotion windows; transcript remains usable | Remote or local model runtime |
| `audio.stem_separation` | Demucs on CPU (slower), or any remote stems API through an `async_job` template (Tier 1) | Local CPU `local` route (free); `async_job` for remote | No stem control: the editor's stem action is disabled with an unavailable message, the Audio tab keeps the full mix, renders are unchanged | No, CPU works |
| `image.generation` | Remote image API | `openai_compatible` or `generic_http` | Feature is unavailable/hidden; no current required caller fails | Remote today |
| `video.generation.submit/status` | Remote async generation API through `async_job`: Tier 1 if the provider fits the blocking slice, otherwise Tier 3 (Task 28) | `async_job` | Generated B-roll control disabled with clear unavailable state | Remote today |
| `matte.segmentation` | MediaPipe CPU route via the existing CPU vision topology | Local CPU `local` route | Matte-dependent background effects are disabled | No, CPU works |
| `audio.sound_classification` | Existing YAMNet TFLite CPU path | Local CPU `local` route | Labels are empty and audio intelligence is degraded | No, CPU works |

A no-GPU production acceptance profile must configure remote planning and transcription, and must either configure remote or CPU routes for any feature the operator wants enabled. No GPU Compose service may be required by startup validation when `GPU_WORKERS_ENABLED=false`.

**Tier notes (revision 3):**

- **After Tier 1:**
  - Planning and transcription are available.
  - The CPU-local routes are available: matte, Demucs stems and YAMNet sound.
  - Object detection, diarization and remote stems can be configured for any provider whose API fits the Tier 1 slice (Section 2.10). Video generation can too, but only in blocking mode.
  - Captions, voice emotion and remote embeddings return in Tier 2.
- **Embeddings without a GPU:** the cpu-vision worker image excludes torch, TensorFlow and transformers (`apps/api/pyproject.toml`, the `cpu-vision` extra). Local CLIP embeddings are therefore not available on a no-GPU deployment without a custom image, so plan on a remote embeddings route (Tier 2).
- **No-route fallbacks for the three ported capabilities:** these already exist in code, and Task 17 keeps them:
  - Visual analysis returns empty detections with `unavailable:not_configured` (`apps/api/app/services/remote_visual.py`).
  - Diarization uses `heuristic_single_speaker_v1` (`apps/api/app/services/audio_intelligence.py`).
  - The Audio tab already shows a hint when no stem exists (`apps/web/components/editor/tabs/audio-tab.tsx`).
  - Task 17 adds an API check, so a stem request without a route is refused at once instead of failing later in the worker.

---

## 4. Dependency-ordered implementation plan

Task numbers are kept from revision 1 so earlier references stay valid. Split tasks use letter suffixes (6L, 9A/9B, 16A/16B). Task 13 is deleted, and Task 28 is new in revision 3. Each task lists its dependencies, files (actual paths, or **NEW (proposed)**), migrations, tests, rollout/back-compat, risks and acceptance criteria. A task depends only on tasks that come earlier in the execution order below, in the same tier or an earlier one.

### 4.0 Tiers, execution order and gates

| Tier | Execution order | Exit gate |
|---|---|---|
| **Tier 1 — MVP** | 1 → 2 → 3 → 4 → 5 → 6 → (6L, Path A only) → 7 → 8 → 9A → 10 → 14 → 15 → 16A → 16B → 17 → 18 → 19 → 26 → 27 | Tier 1 gate, end of 4.1 |
| **Tier 2** | 9B → 11 → 12 | Tier 2 gate, end of 4.2 |
| **Tier 3** | 28 → 20 → 21 → 22 → 23 → 24 → 25 | Tier 3 gate, end of 4.3 |

Changes to the requested tier lists, each made to fix a dependency:

| Change | Reason |
|---|---|
| Task 9 split: 9A (privacy metadata) to Tier 1, 9B (UI gating) to Tier 2 | Task 27 scans `apps/web`, and the privacy page hard-codes five processor names |
| Task 14 added to Tier 1 | Task 27 needs local model ids out of code; Task 14 also keeps CPU matte, stems and sound classification available without a GPU |
| Task 16 split: 16A (operation records, async gateway rules, migration) and 16B (`async_job` slice), both in Tier 1 | Task 18 needs 16A; Task 17 needs 16B |
| Tasks 15 (minimum slice), 16B (minimum slice, blocking mode) and 17 (required) added to Tier 1, before Task 19 | Task 19 deletes the three vendor adapters, and Task 27 (in Tier 1) requires them gone. Task 17 must therefore pass in Tier 1, so its prerequisites 15 and 16B must be in Tier 1 too |
| The rest of the old Tasks 15 and 16B becomes Task 28 in Tier 3 | Keeps Tier 1 to what the three ports need |
| Task 6 keeps only the runtime; the translator becomes Task 6L | The default Path B has no translator (Section 0.4) |
| Task 19 depends on Task 17 | The vendor files are deleted only after the conformance and differential tests pass |

**Tier 1 scope change (revision 3).** Revision 2 said Tier 1 ships no mapping language. Tier 1 now includes the minimum `generic_http` and `async_job` slices (Section 2.10) and the three template ports, so roughly three more tasks of work. There is no smaller option: Task 27, the Tier 1 exit check, requires the vendor files to be gone, and Task 19 may delete them only after Task 17 passes. The only way to make Tier 1 smaller is to move Tasks 19 and 27 out of it.

| Task | Tier | Depends on |
|---|---|---|
| 1 | 1 | — |
| 2 | 1 | 1 |
| 3 | 1 | 1 |
| 4 | 1 | 3 |
| 5 | 1 | 4 |
| 6 | 1 | 3, 4, 5 |
| 6L (Path A only) | 1 | 6 |
| 7 | 1 | 6 |
| 8 | 1 | 7 |
| 9A | 1 | 4, 8 |
| 10 | 1 | 6, 7, 8 |
| 14 | 1 | 4, 6, 7 |
| 15 | 1 | 4, 5, 7, 8 |
| 16A | 1 | 3, 6, 7 |
| 16B | 1 | 15, 16A |
| 17 | 1 | 8, 14, 15, 16B |
| 18 | 1 | 8, 10, 16A |
| 19 | 1 | 7, 8, 10, 14, 17, 18 |
| 26 | 1 | 9A, 10, 14, 19 |
| 27 | 1 | 2, 9A, 26 |
| 9B | 2 | 8, 18 |
| 11 | 2 | 6, 8, 9A, 19 |
| 12 | 2 | 6, 8, 9A, 14, 19 |
| 28 | 3 | 15, 16B |
| 20 | 3 | 4, 5, 7, 16A |
| 21 | 3 | 16A, 20 |
| 22 | 3 | 8, 20, 21 |
| 23 | 3 | 9B, 21, 22 |
| 24 | 3 | 7, 22 |
| 25 | 3 | 5, 7, 22 |

**Path A ordering exception:** Tasks 11 and 12 move before Task 19 and drop their dependency on it, so live users keep those features.

### 4.1 Tier 1 — MVP

Tier 1 delivers:

- a file-based registry, with secret references through secret files or a bundle;
- capability validation and `/capabilities`;
- gateway-only planning and transcription;
- object detection, diarization and stem separation ported to any-provider templates;
- the removal of Runway.

It must work with remote APIs only and no GPU.

#### 1. Establish a shared fake adapter and no-network CI contract

**Dependencies:** none.

**Files:**

- Reuse and consolidate `apps/api/tests/test_model_gateway.py` and `apps/api/tests/test_model_gateway_feature_budgets.py` fake adapters.
- **NEW (proposed):** `apps/api/app/services/inference_fake.py` or a neutral fake implementation under the existing API service tree.
- **NEW (proposed):** `apps/api/tests/support/fake_provider_server.py`.
- `apps/api/tests/conftest.py`.

**Migration:** none.

**Implementation:** fake every typed capability, including success, schema-invalid response, 400, 401/403, 402, 429, 5xx, timeout-before-send and timeout-after-send, plus async submit and status. Make normal tests fail if they open a real network socket.

**Tests:** gateway route selection, priority fallback, budgets, quota guard, circuit opening, typed schema validation and async submit/status behavior.

**Rollout/back-compat:** test/dev only. Production validation rejects fake routes.

**Risks:** a fake response can drift from a real protocol. Keep response fixtures and adapter contract tests together.

**Acceptance criteria:** with all real keys unset and network blocked, the API inference test suite passes and every listed capability can produce a schema-valid fake result.

#### 2. Add a vendor-neutrality ratchet scanner

**Dependencies:** 1.

**Files:**

- **NEW (proposed):** `scripts/verify/vendor_neutrality.py`.
- **NEW (proposed):** `scripts/verify/vendor-neutrality-allowlist.txt`.
- Existing verification stage manifest `scripts/verify/stages.json`.
- Existing `tests/gpu-stage.json` and `tests/gpu-stage-allowlist.json` if their scope checks need updating.

**Migration:** none.

**Implementation:** scan runtime, config, UI, infra and test source with word-boundary patterns for the audited vendor names. Allowed categories:

- adapter implementation files and their tests;
- registry files under **NEW (proposed)** `infra/ai-registry/` and `docs/ai-registry/`, where vendor and model names are configuration data;
- `docs/**` and `*.md`;
- lock files;
- historical applied migrations, reported separately because they cannot be rewritten;
- Path A only: Task 6L, with an expiry at Release N+2.

Protocol terms such as `openai_compatible` are not findings.

**Tests:** scanner unit tests for word boundaries, allowlist path matching, generated files and a deliberate forbidden string.

**Rollout/back-compat:** report-only baseline first. Enforcement starts in Task 27 (Tier 1 gate) and the scan re-runs at the Tier 2 and Tier 3 gates.

**Risks:** false positives in model ids and historical data. Show every allowlisted match in the report with its reason.

**Acceptance criteria:** the report names every current finding; a new audited vendor string in core API, web, infra or a new migration fails the enforce mode.

#### 3. Introduce the fine-grained Motionly capability taxonomy

**Dependencies:** 1.

**Files:**

- `apps/api/app/services/model_gateway.py`.
- `apps/api/app/services/model_provider_bridges.py`.
- `scripts/verify/vulture_whitelist.py` (temporary re-export shims).
- **NEW (proposed):** `apps/api/app/services/inference_capabilities.py`.
- **NEW (proposed):** `apps/api/app/services/inference_schemas.py`.

**Migration:** none.

**Implementation:** add all capability ids from Section 2.1; add typed requests/responses for captions, emotion, status and image generation; provide aliases from old `ModelOperation` values; ensure `capability_for_request` and `operation_for_request` are exhaustive.

**Tests:** every capability has request validation and response validation; old serialized operation names remain readable; metadata emits new ids; embedding vectors enforce aligned dimensions.

**Rollout/back-compat:** old classes and operation aliases are re-exported only while callers move within Tier 1, and are deleted before the Tier 1 gate (Path A: kept until Release N+2). Historical operation strings in `provider_slo_events` are not rewritten.

**Risks:** broad type churn. Keep compatibility imports until all callers move.

**Acceptance criteria:** a table-driven test covers every capability and no request type falls through to a generic untyped dictionary.

#### 4. Add a declarative registry file schema and validator

**Dependencies:** 3.

**Files:**

- `apps/api/app/core/config.py` (`AI_REGISTRY_FILE`).
- `apps/api/app/services/model_gateway.py`: move the revision rule out of `ModelRoute.__post_init__` into the validator (Section 2.6, "Current code to change").
- **NEW (proposed):** `apps/api/app/services/inference_registry.py`.
- **NEW (proposed):** `docs/ai-registry-example.json`.
- **NEW (proposed):** `apps/api/app/ops/validate_inference_registry.py` (validator CLI).

**Migration:** none.

**Implementation:**

- **Format:** JSON only in Tiers 1–2, so no new dependency. Version the format (`"version": 1`) and reject unknown major versions.
- **Records:** adapters (Section 2.2), models (Section 2.9) and routes (Section 2.3), including `allow_provider_managed_revision`, `pricing.status` and `budget.hard_cap`.
- **Locality:** derived as in Section 2.6.
- **Output:** a list of `{route_id, severity, code, message}`. `APP_ENV` decides whether missing data-processor metadata is a warning or an error.
- **Other checks:** ids, references, adapter/capability support, HTTPS or private network, embedding space and GPU requirement.

**Tests:** one valid and one invalid fixture per Section 2.6 rule, asserting severity and code; duplicate ids; unknown references; URL credentials or query strings; missing auth.

**Rollout/back-compat:** used from Task 6 onward. Path A: Task 6L produces records in this schema.

**Risks:** the file format becomes a public contract. Keep it versioned.

**Acceptance criteria:** the example registry loads deterministically; every Section 2.6 rule has fixtures with the expected severity; invalid optional routes produce diagnostics without hiding errors in required routes; the CLI exits non-zero only on errors.

#### 5. Add secret references, authentication strategies and generic redaction

**Dependencies:** 4.

**Files:**

- `apps/api/app/core/config.py` generic secret-file settings.
- `apps/api/app/core/logging.py`.
- `apps/api/app/services/model_adapters.py`.
- `apps/worker/src/log-redaction.mjs` and its tests.
- **NEW (proposed):** `apps/api/app/services/inference_secrets.py`.
- **NEW (proposed):** `apps/api/app/services/inference_auth.py`.

**Migration:** none.

**Implementation:** support `none`, bearer, custom header, Azure api-key and OAuth2 client credentials. Resolve `file:` and `bundle:` refs; `db:` refs come in Task 20 and SigV4 in Task 25. Register resolved secret values with redaction. Reject `env:` secret refs in production.

**Tests:** missing ref, file ref, auth header construction, OAuth token caching/refresh, exact secret redaction in logs/reprs/exceptions, no secret in route responses.

**Rollout/back-compat:** Path B uses only generic secret files or a bundle. Path A: Task 6L reads the old `_FILE` variables for one release.

**Risks:** leaked secrets through exception context or request logging. Add tests around every auth failure and ensure middleware does not log request bodies.

**Acceptance criteria:** an injected test secret is absent from all captured logs, error messages, serialized route metadata and fake-provider request logs, except the in-memory request sent to the intended fake server.

#### 6. Build one registry-driven runtime

**Dependencies:** 3, 4, 5.

**Files:**

- `apps/api/app/services/model_gateway_factory.py`: replaced by the runtime; Task 26 deletes any wrapper left behind.
- `apps/api/app/main.py`, `apps/api/app/workers/visual_job.py`, `apps/api/app/workers/matte_job.py`: all build the gateway from the registry.
- **NEW (proposed):** `apps/api/app/services/inference_runtime.py`.

**Migration:** none.

**Implementation:** the registry file is the only source of routes (Path B). The API and the workers use the same builder and route catalog fingerprint. Workers report registry version and fingerprint in their heartbeat, and fail closed on a mismatch for required capabilities. `OPENROUTER_BASE_URL`, `GROQ_BASE_URL` and `_legacy_routes()` go away with the factory.

**Tests:** the API and both worker entrypoints build identical catalogs from one registry; a mismatch fails closed; planning and transcription are served by fake routes.

**Rollout/back-compat:** Path B: outside tests, startup fails if `AI_REGISTRY_FILE` is missing. Path A: Task 6L translates the old settings for one release.

**Risks:** API/worker drift, covered by the fingerprint check.

**Acceptance criteria:** the API and the visual and matte workers build the same route catalog from one registry file and serve planning and transcription against the fake provider.

#### 6L. Legacy environment translator (Path A only)

**Dependencies:** 6. **Built only if live production users or data exist.**

**Files:**

- **NEW (proposed):** `apps/api/app/services/legacy_inference_config.py`.
- **NEW (proposed):** `apps/api/app/ops/convert_legacy_inference_config.py`.

**Migration:** none.

**Implementation:**

- When no registry file is configured, translate today's settings into registry records and log a deprecation warning. Keep route ids and revisions, so caches and stored provenance stay valid.
- The converter writes a registry file and a list of secret references without printing secret values.
- Map old quota setting names: `QUOTA_OPENROUTER_*` to `QUOTA_PLANNING_*`, and `QUOTA_RUNWAY_*` to `QUOTA_VIDEO_GENERATION_*`.

**Tests:** parity between legacy settings and the converted registry: routes, prices, operations, fingerprints and required-route behavior.

**Rollout/back-compat:** Release N only; deleted in Release N+2 by Task 26.

**Risks:** the translator outlives its release. The scanner allowlists it only with an expiry.

**Acceptance criteria:** a legacy configuration and its converted registry produce the same planning and transcription behavior against the fake provider.

#### 7. Replace vendor startup checks with capability validation

**Dependencies:** 6.

**Files:**

- `apps/api/app/core/production.py`: remove the vendor key, provider-value, Runway, vendor pricing and vendor revision checks. Keep the unrelated Supabase, Redis, Temporal, billing and worker checks.
- `apps/api/app/core/config.py`.
- `apps/api/app/main.py`: production `required_operations` shrinks to planning and transcription.
- `apps/api/app/services/model_gateway.py`: hard-cap enforcement for unknown-pricing routes, and `cost_unknown` in telemetry.
- **NEW (proposed):** `apps/api/app/services/inference_validation.py`.
- Existing tests `apps/api/tests/test_service_hardening.py`, `test_gpu_free_deployment.py`, `test_health.py`.

**Migration:** none.

**Implementation:** apply every Section 2.6 rule with its severity. A required capability without a valid route fails startup. An invalid optional route is dropped with a structured warning. The unknown-pricing hard cap uses the existing budget ledger with a per-route monthly scope counted in requests or billing units. When the cap is reached, the route is refused with `route_hard_cap_exhausted` and fallback continues.

**Tests:** the full Section 2.6 matrix:

- missing planning or transcription route;
- `provider-managed` remote route with and without the opt-in flag;
- local route with `latest` or `provider-managed`;
- local route without licence (error) and remote route without licence (warning);
- remote route without data-processor metadata in dev (warning) and in production (error);
- zero price without `explicitly_free`;
- unknown pricing with and without a hard cap, and the cap being reached;
- fake adapter in production;
- GPU route with `GPU_WORKERS_ENABLED=false`;
- free planning route on a private adapter (valid) and on a remote adapter in production (error).

**Rollout/back-compat:** Path B: operators ship a registry with planning and transcription routes in the same release. Path A: Task 6L supplies them during Release N.

**Risks:**

- Optional routes could disappear silently. Every dropped route is logged with its id and shown in `/capabilities`.
- Unknown-pricing spend is invisible to dollar budgets. The hard cap is the only control, and a warning is logged at startup.

**Acceptance criteria:** production boots with only registry routes for planning and transcription, with secrets from files; one of the routes uses `provider-managed` with the flag and logs exactly one warning. Each Section 2.6 rule has a passing and a failing case with the documented severity.

#### 8. Add `/capabilities` and registry-driven provider health

**Dependencies:** 7.

**Files:**

- `apps/api/app/api/health.py`.
- `apps/api/app/main.py`.
- Existing `apps/api/app/services/ai_features.py` is replaced or reduced to a compatibility wrapper.
- **NEW (proposed):** `apps/api/app/api/capabilities.py`.
- **NEW (proposed):** `apps/api/app/services/inference_capability_report.py`.
- `packages/shared/openapi.json`, `packages/shared/src/generated/api.ts`, regenerated with `pnpm contracts:generate`.

**Migration:** none.

**Implementation:** `GET /capabilities` exposes status, reason, async flag, compute requirement and route count. Health uses adapter-declared probe requests and circuit state, with no vendor list. The authenticated `/health/dependencies` report adds an inference entry with validation warnings and the count of `submit_unknown` operations. Public liveness and readiness stay inexpensive, and authenticated diagnostics stay protected as today.

**Tests:** capability matrix with empty, valid, failed and circuit-open routes; no secret or base URL leakage; fake health responses; OpenAPI contract regeneration.

**Rollout/back-compat:** health response adds fields without removing existing ones. The existing `gpu_stage` field stays until the UI no longer relies on it.

**Risks:** endpoint information disclosure. Only capability-level data is public; detailed route status is admin-only.

**Acceptance criteria:** an optional capability with no configured route reports `unavailable` with a machine-readable reason, and a route with an open circuit reports `degraded` without making the whole API unhealthy.

#### 9A. Config-driven privacy metadata

**Dependencies:** 4, 8.

**Files:**

- `apps/web/app/privacy/page.tsx`: today it hard-codes sections for Google Cloud Vision, OpenRouter, Jina AI, pyannoteAI and LALAL.AI.
- **NEW (proposed):** `GET /public/data-processors` in `apps/api/app/api/capabilities.py` (Task 8's new router).
- Web source tests under `apps/web/tests/`.

**Migration:** none.

**Implementation:** the privacy page keeps its general sections and renders one section per data processor of the enabled remote routes. The content comes from registry `data_processor` metadata: display name, purpose, data categories and policy URL. The endpoint returns display metadata only: no base URLs, models or secrets. In production every enabled remote route has this metadata (Section 2.6).

**Tests:** the page lists exactly the configured processors; no processor name is hard-coded; no URL or secret appears in the response.

**Rollout/back-compat:** additive. The page follows the registry.

**Risks:** legal copy accuracy. Operators own the metadata, and the production rule blocks a remote route without it.

**Acceptance criteria:** with a registry of two remote routes from different processors, the privacy page shows exactly those two and contains no hard-coded processor names.

#### 10. Gateway-only planning and transcription, and feature-keyed quotas

**Dependencies:** 6, 7, 8.

**Files:**

- `apps/api/app/services/model_provider_bridges.py`: bridges take only a gateway and typed requests.
- `apps/api/app/services/edit_planner.py`: remove the direct HTTP path and its `check_quota(provider="openrouter", category="tokens")`.
- `apps/api/app/services/transcription_provider.py`, `apps/api/app/main.py`: remove the direct fallbacks; dev uses the fake route.
- `apps/api/app/services/billing.py`: Decision B-1.
- `apps/api/app/core/config.py`: `QUOTA_PLANNING_*` and `AI_QUOTA_SCOPE`.
- Tests: `test_plans.py`, `test_phase3_ai_ml_reliability.py`, `test_feature_quotas.py`, `test_model_gateway.py`, `test_route_callers.py`.

**Migration:** **NEW (proposed)** index `cost_attribution_events(workspace_id, category, created_at desc)`, with a `supabase/tests/` check.

- The existing indexes lead with `(workspace_id, provider, category, …)` (`202606220008_phase_8_operations.sql`, `202607050003_phase_2_upload_cost_indexes.sql`), so counting by category without the provider cannot use them efficiently.
- The `tokens` category is already allowed by `202610220101_remote_ai_feature_quota_categories.sql`, so no constraint change is needed.

**Decision B-1 — plan quotas are keyed by feature, not by provider.**

- **Today:** `PlanFeatureQuotaGuard.check` sums `count_usage(provider=route.provider, category=…)`, and `PROVIDER_QUOTAS` is keyed `runway` and `openrouter`. Planning tokens are effectively unmetered: the gateway bridge passes `billing_repository=None` (`model_provider_bridges.py`), so the token check is skipped, and no code records `tokens` rows.
- **Decision:** quotas count per Motionly feature: `planning`, `vision_objects`, `vision_captions`, `embeddings`, `audio_diarization`, `audio_emotion`, `audio_stems`, and `video_generation` (added in Task 18). The provider label stays on each usage row as metadata only. Transcription stays unmetered by plan, as today.
- **Why:** provider-keyed counting resets a workspace's quota whenever the operator changes provider, and it puts vendor names into billing keys.
- **Impact (one line):** workspaces whose use of one feature was split across several provider labels in a billing period may hit limits sooner because that usage is now summed, and every workspace that uses planning may hit the planning limit for the first time because planning tokens are not counted today.
- **Temporary flag:** `AI_QUOTA_SCOPE=feature|provider`, default `feature`.
  - `provider` restores today's per-provider counting; the new planning quota is still recorded but not enforced.
  - Both modes read the same rows, because each row stores both the provider label and the category, so switching is lossless.
  - Remove the flag at the Tier 2 gate, and no earlier than one full billing period after Tier 1 ships.
- **Settings:** `QUOTA_OPENROUTER_*` becomes `QUOTA_PLANNING_*` (Path A: Task 6L maps the old names for one release).

**Implementation:** production planning and transcription go only through the gateway. The planning route records token usage through the quota guard.

**Tests:** end-to-end fake upload → transcript → plan. Switching the planning route's provider label keeps accumulated usage under `feature` and resets it under `provider`. Planning usage rows are written in both modes. The new index exists.

**Rollout/back-compat:** announce the impact line to workspace owners before shipping. Use `AI_QUOTA_SCOPE=provider` only as a stop-gap.

**Risks:** planning limits start applying, and today's `QUOTA_OPENROUTER_*` default values, carried into `QUOTA_PLANNING_*`, have never been exercised. Review them before shipping.

**Acceptance criteria:** planning and transcription work against two routes with different provider labels without consumer changes; quota usage is continuous across the switch under `feature`; the flag behaves as documented.

#### 14. Move local models and licence metadata into routes

**Dependencies:** 4, 6, 7.

**Files:**

- `apps/api/app/services/model_domain_adapters.py`: the local `register_*` functions for matte, stems, CLIP, visual manifest and audio intelligence become registry `local` routes.
- `apps/api/app/core/config.py`: local model fields `MATTE_*`, `YAMNET_*`, `VISUAL_CLIP_*`, `VISUAL_BLIP_*`, `VISUAL_YOLO_*`, `PYANNOTE_*`, `WAV2VEC_*`, `DEMUCS_*`.
- `apps/api/app/services/stock_video.py`, `apps/api/app/services/layer_jobs.py`, `apps/api/app/api/broll.py`: hard-coded `openai/clip-vit-base-patch32` defaults.
- `apps/api/app/services/ai_features.py`: GPU checks become the `requires_gpu` flag.
- `apps/api/app/workers/prefetch_models.py`: reads the registry.
- **NEW (proposed):** `infra/ai-registry/cpu-local.json` (MediaPipe matte, YAMNet TFLite, Demucs) and `infra/ai-registry/gpu-local.json` (CLIP, BLIP, YOLO, pyannote, wav2vec2).

**Migration:** none.

**Implementation:** local routes follow the strict Section 2.6 and 2.9 rules: an immutable revision or SHA, a weights SHA-256 wherever Motionly downloads the weights, and required licence and commercial-use metadata. `requires_gpu` is the only placement signal. The cpu-vision image excludes torch, TensorFlow and transformers (`apps/api/pyproject.toml`), so CLIP appears only in the GPU registry. Demucs runs in the existing `demucs-worker` image (`apps/worker/Dockerfile.python.demucs`).

**Tests:** a no-GPU boot with `cpu-local.json` reports matte, stems and sound available; GPU routes are dropped with `GPU_WORKERS_ENABLED=false`; a local route with `latest`, `provider-managed` or no licence is rejected; a non-commercial licence warns (or errors with `AI_BLOCK_NONCOMMERCIAL=true`); prefetch reads the registry. Update `apps/api/tests/test_cpu_matte.py`, `test_cpu_vision_image.py` and `test_gpu_free_deployment.py`.

**Rollout/back-compat:** Path B deletes the local model variables. Path A: Task 6L maps them for one release.

**Risks:** licence data needs manual verification from authoritative sources. It is never inferred from model names.

**Acceptance criteria:** a no-GPU deployment with `cpu-local.json` starts CPU matte, Demucs stems and YAMNet sound without loading GPU-only packages; every enabled local model has a pinned revision and licence metadata; no local model id remains as a code default.

#### 15. `generic_http` adapter — Tier 1 slice

**Dependencies:** 4, 5, 7, 8.

**Files:**

- **NEW (proposed):** `apps/api/app/services/generic_http_adapter.py`.
- **NEW (proposed):** `apps/api/app/services/inference_templates.py` (the Section 2.10 Tier 1 grammar).
- **NEW (proposed):** `apps/api/app/services/inference_normalization.py` (the per-capability normalizers in Section 2.10).
- `apps/api/app/services/media_url_policy.py`: reuse `safe_http_stream` and its IP rules for captured URLs.

**Migration:** none.

**Implementation:**

- Build exactly the Tier 1 `generic_http` slice from Section 2.10: JSON request bodies, aligned results, `vertices_norm`/`xywh_norm` boxes, per-item errors, `error_map` and item-count usage.
- The validator (Task 4) rejects unknown keys and functions before a route is activated.
- Per-route `limits.max_batch` and `limits.max_request_bytes` are read by the callers that split batches. `GatewayObjectsEnricher` in `apps/api/app/services/remote_visual.py` already splits by count (16) and size (9 MiB) today, so these become route values instead of constructor defaults.

**Tests:**

- Every allowed value form and function.
- Rejection of every disallowed construct: host change, unknown function, environment or secret access, a `$map` over more than `max_batch` items.
- Partial and total item errors, both box formats, oversized responses and the error map.
- Normalizer behavior.

**Rollout/back-compat:** additive.

**Risks:** grammar creep. Anything outside Section 2.10 belongs in Task 28 or needs a plan change.

**Acceptance criteria:** a fake synchronous object-detection API is integrated with registry config and fixtures only. `vision.object_detection` becomes available, and its output matches the normalized detection schema.

#### 16A. Persisted async operations and route-affine gateway rules

**Dependencies:** 3, 6, 7.

**Migration:** **NEW (proposed):** `supabase/migrations/<timestamp>_inference_operations.sql` and `supabase/tests/inference_operations_test.sql`, with:

- id, workspace/project references and RLS
- `capability`, and `idempotency_key` unique per workspace
- `provider_route_id`, `provider_model`, `provider_model_revision`
- `route_snapshot` JSONB: adapter id/type/base URL/model/revision/mapping version and the secret reference name, never a secret value
- `provider_operation_id`, unique per route where not null
- `state` check: `reserved`, `submitting`, `submitted`, `submit_unknown`, `running`, `succeeded`, `failed`, `cancelled`, `needs_review`
- status/error detail, estimated/actual cost, timestamps
- worker grants following the least-privilege pattern in `supabase/migrations/202610230101_task18_worker_least_privilege.sql`

**Files:**

- `apps/api/app/services/model_gateway.py`: today `invoke` stops falling back only for `VideoGenerationSubmitRequest`. Generalize this to every capability flagged as an async submit and to every failure after send.
- `apps/api/app/services/provider_resilience.py`: add a `sent` flag to `ProviderErrorInfo`.
- **NEW (proposed):** `apps/api/app/services/inference_operations.py` (state machine and repository).

**Implementation:**

- Reserve the operation before the submit.
- Mark the operation `submit_unknown` on any failure after the request may have been sent.
- Status calls use the stored snapshot even if the route was edited or disabled, and are not billed (current behavior).
- No adapter is added here.

**Tests:** with the Task 1 fake async adapter:

- a double submit returns the same operation;
- a timeout after send produces `submit_unknown`, and the fake asserts that no second route was contacted;
- a status call after a registry change uses the snapshot;
- RLS and worker grants hold.

**Rollout/back-compat:** additive table in both paths.

**Risks:** operations stuck in `submit_unknown`. Their count appears in `/health/dependencies`, and they are reconciled by polling the original route.

**Acceptance criteria:** a fake async job produces a complete persisted state trace, and no retry after the send point reaches a different route.

#### 16B. `async_job` adapter — Tier 1 slice, blocking mode

**Dependencies:** 15, 16A.

**Files:**

- **NEW (proposed):** `apps/api/app/services/async_job_adapter.py`.
- **NEW (proposed), from Task 15:** `apps/api/app/services/inference_templates.py`.
- `apps/api/app/services/subprocess_env.py`: `media_tool_env()` for `input_transform.audio`.
- `apps/api/app/services/media_url_policy.py`: SSRF policy for captured upload and artifact URLs.

**Migration:** none; uses the Task 16A table.

**Implementation:** exactly the Tier 1 `async_job` slice in Section 2.10, in blocking mode only:

- The `vars` block, ordered steps, `capture` and `require`.
- Per-step `auth: none` for pre-signed URLs.
- `commit`, then polling with a status map, tolerance for transient errors, backoff and a deadline.
- Artifacts with host, HTTPS and size limits.
- `cleanup` that runs after success and after failure.
- `input_transform.audio` and an adapter-managed temporary directory, removed on every exit path.

Each call records its job in `inference_operations` (Task 16A). When a worker retries after the commit step, it resumes polling the recorded job instead of submitting again.

**Tests:**

- A config-only async job against the fake server.
- A retry after commit never resubmits, and the fake confirms no second route was contacted.
- `auth: none` steps send no adapter credentials.
- Captured URLs pointing at a disallowed host or a private IP are refused.
- Artifact size cap and a missing required artifact.
- Cleanup and temp-directory removal on success, on failure and on timeout.

**Rollout/back-compat:** additive.

**Risks:**

- Duplicate charges: covered by the commit point and Task 16A.
- Long blocking waits hold a worker slot: `max_wait_s` limits this, and non-blocking mode is Task 28.

**Acceptance criteria:** a config-only blocking async route finishes end to end against the fake with a complete operation trace. No retry after commit reaches the provider's submit endpoint twice, and no temporary file remains.

#### 17. Port object detection, diarization and stem separation to templates (required)

**Dependencies:** 8, 14, 15, 16B.

**Goal:** all three capabilities work with any model from any provider, configured in the registry only, with no provider-specific code. Today's three integrations become documented example routes, and their behavior is kept exactly.

**Step 0 — archive first.** Before any change, commit the current state of the vendor files to a branch and tag it `archive/pre-neutral-inference`. Push the tag. These files are untracked today (`git status` shows `??`):

- `apps/api/app/services/google_vision.py`
- `apps/api/app/services/pyannoteai_diarization.py`
- `apps/api/app/services/lalal_stems.py`
- `apps/api/tests/test_google_vision_objects.py`
- `apps/api/tests/test_pyannoteai_diarization.py`
- `apps/api/tests/test_lalal_stems.py`

The tag is the reference implementation for the differential tests below.

**Files:**

- **NEW (proposed):** `docs/ai-registry/examples/object-detection.json`, `docs/ai-registry/examples/diarization.json` and `docs/ai-registry/examples/stem-separation.json`, with scenario fixtures ported from the three test files above.
- **NEW (proposed):** `apps/api/tests/test_inference_template_conformance.py`. It finds every example and scenario by glob and contains no vendor names.
- **NEW (proposed):** `apps/api/tests/test_inference_template_differential.py`. It runs each scenario through both the archived adapter and the template on the same mock transport and compares the results. Task 19 deletes it together with the archived adapters.
- **NEW (proposed):** `apps/api/app/services/audio_diarization.py`, which receives the neutral wrapper (`PyannoteAIDiarizationProvider`'s degrade-instead-of-raise behavior) under a neutral name.
- `apps/api/app/services/remote_visual.py`: the objects enricher is enabled by an `vision.object_detection` route; batch limits come from the route.
- `apps/api/app/services/scale.py`: stem jobs use the `audio.stem_separation` route; the temp-folder prefix becomes generic.
- `apps/api/app/api/scale.py`: `separate_audio_stems` returns `503 capability_unavailable` when no route exists. Today the job is queued and fails later in the worker with `stem_gateway_route_required`.
- `apps/api/app/services/model_gateway.py`: `DiarizationRequest.minimum_billable_s` is replaced by route `pricing.minimum_billable_units`.

**Migration:** none.

**Behavior parity** (each row has at least one conformance scenario and one differential scenario):

| Capability | Behavior today | Where it is kept |
|---|---|---|
| Objects | At most 16 images and about 9 MiB per request; the enricher splits batches | Route `limits.max_batch` and `limits.max_request_bytes` (Task 15) |
| Objects | One response per image, in order; a length mismatch is a malformed response | `aligned_to_input: true` |
| Objects | Per-image errors: if all images fail, codes 3, 9 and 11 mean rejected (not billed, no fallback) and other codes mean unavailable (retryable, not billed); if some fail, those images get no detections and a warning is logged | `item_error` and `item_error_codes` |
| Objects | Only images that were annotated are billed | `usage: count_ok_items` |
| Objects | Box from normalized vertices; a missing coordinate counts as 0; clamp to 0–1 | `box_format: vertices_norm` and the normalizer |
| Objects | Labels lowercased and cut to 80 characters, with an alias map to COCO names; sorted by score; top N | Object-detection normalizer and `label_aliases` |
| Diarization | Random opaque media name; no user or file name is shared | `vars` block with `concat` and `uuid()` |
| Diarization | Reserve an upload, require an HTTPS upload URL, upload without the API key | Steps with `capture`, `require: https` and `auth: none` |
| Diarization | Submit; from here the job is billable and must not fall back | `commit: true` |
| Diarization | Poll: running states continue; failed and cancelled are not billed; unknown states are malformed; transient errors are tolerated; 5 s growing ×1.5 to 30 s; 900 s limit | `poll` with status map, `tolerate_retryable`, backoff and `max_wait_s` |
| Diarization | Seconds to milliseconds; segments sorted | `to_ms` and the diarization normalizer |
| Diarization | 20-second minimum billing | Route `pricing.minimum_billable_units` |
| Stems | Audio-only, 44.1 kHz stereo FLAC before upload; video never leaves the worker | `input_transform.audio` |
| Stems | Raw upload with a neutral filename | `body: input.file` and `upload_filename` |
| Stems | Split request with the pinned model settings and an idempotency key | Route `params` and `uuid()` |
| Stems | POST polling; status keyed by the task id; server errors retryable but no fallback | Path segment `{vars.task_id}` and status map with `fallback: false` |
| Stems | Both tracks required and mapped to `vocals`/`no_vocals`; each under 2 GB and non-empty; provider host only; HTTPS forced; no key sent | `artifacts` with `name_map`, `required`, `max_bytes`, `allowed_hosts`, `force_https`, `auth: none` |
| Stems | The uploaded source is always deleted at the provider afterwards, best effort | `cleanup` |
| Stems | Local temp folder removed on any failure | Adapter-managed temp directory |

**One improvement:** the stems integration creates a new idempotency key on every attempt today, so a worker retry can submit and pay twice. The ported version records the job (Task 16A), so a retry resumes polling.

**No-route behavior** (each capability is `unavailable/not_configured` in `/capabilities`, and editing continues):

- **Objects:** empty detections. The visual manifest lists `objects: unavailable:not_configured` in `degraded_capabilities`, as `remote_visual.py` already does.
- **Diarization:** heuristic speaker labels from the transcript (`heuristic_single_speaker_v1` in `apps/api/app/services/audio_intelligence.py`), marked degraded.
- **Stems:** no stem control. The stem request is refused with `capability_unavailable` (503). The Audio tab keeps its existing hint and the full mix (`apps/web/components/editor/tabs/audio-tab.tsx`), and renders are unchanged. Task 9B (Tier 2) disables the "Separate vocals and music" button in `apps/web/components/editor/ScalePanel.tsx`; until then, pressing it shows the error.
- **Stems with Demucs:** if the CPU-local Demucs route from Task 14 is configured, it serves `audio.stem_separation` and the capability is available.

**Tests:**

- The conformance suite (all scenarios).
- The differential suite against the archived adapters: same outgoing requests, typed outputs, usage, error classes and cleanup calls.
- The no-route behavior for each capability.
- Redaction of every secret.

**Rollout/back-compat:** the example routes keep today's route ids and revision strings (`google-vision-objects-builtin-stable-2026-10-22`, `pyannoteai-precision-v3`, `lalal-andromeda-v6`), so cached visual analyses and stored stem provenance stay valid. Operators enable a capability by copying an example into their registry. Optionally, run `.github/workflows/live-provider-contracts.yml` once against the example routes before Task 19.

**Risks:**

- The grammar is missing something: extend the Tier 1 slice through a plan change, never with provider-specific code.
- A URL returned by a provider points somewhere unexpected (SSRF): `allowed_hosts`, HTTPS and the private-IP policy.

**Acceptance criteria:** every conformance and differential scenario passes. With only registry config, each of the three capabilities works against the fake for the example provider and for a second, differently shaped fake provider. With no route, each capability behaves as described above.

#### 18. Move generated B-roll to neutral async video operations

**Dependencies:** 8, 10, 16A.

**Migration:** two **NEW (proposed)** migrations, each with a matching `supabase/tests/` file:

1. `<timestamp>_stock_provider_generated.sql` adds `generated` to `public.stock_provider`. It must be its own migration, because a new enum value cannot be used in the transaction that adds it. The historical `supabase/migrations/202606210003_phase_5_scale.sql` is not edited.
2. `<timestamp>_neutral_generated_broll.sql`:
   - **Path B (default):**
     - Backfill `stock_assets.provider` and `broll_slots.provider` from `runway` to `generated`.
     - Create `inference_operations` rows from `phase5_resources` rows with `kind='generated_broll'`, copying `runway_task_id` → `provider_operation_id`, `model_gateway_route_id` → `provider_route_id` and `model_revision` → `provider_model_revision`.
     - Replace those payload keys with `inference_operation_id`.
     - Add `CHECK (provider <> 'runway')` to both tables.
     - The migration must be idempotent.
   - **Path A:** the same backfill, but keep the old payload keys and read them alongside the new fields for one release. Add the `CHECK` constraints in Release N+1.
   - **Both paths:** historical `cost_attribution_events` rows keep their recorded provider label as cost history.

**Files:**

- `apps/api/app/services/scale.py`, `apps/api/app/services/job_engine.py`.
- `apps/api/app/api/scale.py`: rename `generate_runway_broll`, use a neutral premium message, and return `503 capability_unavailable` when no `video.generation.submit` route exists.
- `apps/api/app/schemas/broll.py`, `apps/api/app/repositories/broll.py`.
- `apps/api/app/services/billing.py`: a `video_generation` feature quota replaces the provider-keyed entry, and `QUOTA_RUNWAY_*` becomes `QUOTA_VIDEO_GENERATION_*`.
- `packages/shared/src/index.ts`, `packages/shared/openapi.json`, `packages/shared/src/generated/api.ts`, regenerated with `pnpm contracts:generate`.
- `apps/web/components/editor/ScalePanel.tsx`: copy only; gating is Task 9B.

**Implementation:** submit through `video.generation.submit` and poll through `video.generation.status`, bound to the operation's route snapshot (Task 16A). With Task 16B in Tier 1, a video provider can be configured in Tier 1 if its API fits the slice, in blocking mode, with `max_wait_s` set for the provider's typical render time. Non-blocking delayed polling for long jobs is Task 28 (Tier 3). The code path is exercised with the Task 1 fake async adapter.

**Tests:**

- Migrations: backfill idempotency, the constraint and the enum value.
- Existing generated assets still render.
- New jobs fill the three `provider_*` columns.
- A resumed job polls instead of resubmitting.
- With no route, the API returns `capability_unavailable`.
- Contracts regenerate cleanly.
- Rewrite `apps/api/tests/test_scale.py` and `test_phase10_cost_controls.py` on the fake.

**Rollout/back-compat:** Path B: deploy the new code and run both migrations in the single release, after a database backup. Path A: deploy readers and writers that understand `generated` before the backfill; no old writer may run after it.

**Risks:** a rolling-deploy race (Path A) and contract drift in the generated TypeScript.

**Acceptance criteria:** existing generated B-roll renders; with a fake async route, new jobs persist `provider_operation_id`, `provider_route_id` and `provider_model_revision` and never submit twice; with no route, the API returns `capability_unavailable`.

#### 19. Remove Runway, the vendor adapters and the vendor wiring

**Dependencies:** 7, 8, 10, 14, 17, 18.

**Before deleting anything:** many of these files are untracked or have uncommitted changes.

- `git status` shows these as untracked: `apps/api/app/services/google_vision.py`, `pyannoteai_diarization.py`, `lalal_stems.py`, `remote_visual.py`, `remote_audio_emotion.py`, `ai_features.py`, `infra/docker-compose.remote-ai.yml`, and their tests.
- `JinaEmbeddingsAdapter` exists only in the uncommitted changes to `apps/api/app/services/model_adapters.py`; it is not in `HEAD`.

The Git tag `archive/pre-neutral-inference` (created in Task 17, Step 0) must exist on the remote and must contain all of these files. If it does not, stop. Deleting them unarchived would lose the only copy, including the reference behavior that Task 12 needs.

**Scope:**

1. **Runway (required).** Delete:
   - `RunwayGen3Provider`, `FixtureRunwayProvider` and `RunwayResult` in `apps/api/app/services/scale.py`;
   - `RunwayVideoGenerationAdapter` and `register_runway_route` in `apps/api/app/services/model_domain_adapters.py`;
   - the Runway branches in `apps/api/app/main.py` and `apps/api/app/services/job_engine.py`;
   - the `RUNWAY_*` and `QUOTA_RUNWAY_*` settings in `apps/api/app/core/config.py`;
   - the production requirement in `apps/api/app/core/production.py`;
   - the health probe in `apps/api/app/api/health.py`;
   - the `PROVIDER_QUOTAS` entry in `apps/api/app/services/billing.py`;
   - the redaction comment in `apps/api/app/core/logging.py`;
   - `StockProviderKey.RUNWAY` in `apps/api/app/schemas/broll.py` and `apps/api/app/repositories/broll.py`;
   - the `runway_api_key` secret in `infra/docker-compose.gpu.yml`;
   - line 284 of `scripts/deploy/apply_release.sh`;
   - the CI secret in `.github/workflows/live-provider-contracts.yml`;
   - the fixtures `apps/api/tests/fixtures/providers/runway_*.json`, `fixtures/registry.json`, `tests/gpu-stage.json`, `tests/gpu-stage-allowlist.json` and `scripts/verify/evidence_report.py`;
   - the Runway cases in the API tests listed in Section 1.2.
2. **Vendor-specific adapters for object detection, diarization and stems — only after Task 17 passes.** Gate: Task 17's conformance and differential suites pass on the commit that performs the deletion, and the archive tag exists. Then delete:
   - `apps/api/app/services/google_vision.py`, `pyannoteai_diarization.py` and `lalal_stems.py`;
   - their `register_*` functions in `model_domain_adapters.py`, and `_google_vision_objects_enricher` in `remote_visual.py`;
   - their vendor settings and production checks;
   - their secrets in `infra/docker-compose.remote-ai.yml`;
   - `apps/api/tests/test_google_vision_objects.py`, `test_pyannoteai_diarization.py` and `test_lalal_stems.py`, whose scenarios now live in Task 17's fixtures;
   - **NEW (proposed) in Task 17:** `apps/api/tests/test_inference_template_differential.py`, because the adapters it compares against are gone.

   The three capabilities keep working through their template routes (Task 17), and `GatewayObjectsEnricher` stays.
3. **Captions, voice emotion and remote embeddings wiring (rebuilt in Tier 2).** Delete:
   - the vendor factories `_openrouter_captions_enricher` and `_jina_keyframe_embeddings_enricher`, and the vendor switches in `remote_visual_enricher_versions` (`remote_visual.py`);
   - `ensure_openrouter_zdr_adapter`, `register_openrouter_audio_emotion_route`, `register_openrouter_vision_captions_route` and `register_jina_embedding_routes` (`model_domain_adapters.py`);
   - `JinaEmbeddingsAdapter` (`model_adapters.py`) and `apps/api/tests/test_jina_embeddings.py`;
   - `_openrouter_audio_emotion_provider` (`main.py`);
   - their settings.

   Keep the Motionly-owned prompt, schema and parsing code: `GatewayCaptionsEnricher`, `GatewayKeyframeEmbeddingsEnricher`, and the window parsing in `remote_audio_emotion.py`. Rename `OpenRouterAudioEmotionProvider` to a neutral name. Tasks 11 and 12 wire these to capability routes.
4. **Leftovers from Task 10.** Delete the endpoint constants `GROQ_TRANSCRIPTION_ENDPOINT` and `OPENROUTER_CHAT_COMPLETIONS_ENDPOINT` in `apps/api/app/main.py`, and the vendor key-prefix redaction rules in `apps/api/app/core/logging.py` and `apps/worker/src/log-redaction.mjs`.

**Migration:** none beyond Task 18.

**Tests:** delete the Runway and vendor-wiring tests, or rewrite them on fakes. Re-run Task 17's conformance suite after the deletion. Add tests that:

- a missing embeddings route makes B-roll ranking use its lexical fallback instead of failing;
- missing caption or emotion routes leave visual analysis and transcription working, with the capability marked degraded;
- `/capabilities` reports captions, voice emotion and embeddings as `unavailable/not_configured` until Tier 2.

**Rollout/back-compat:** Path B: one release. Path A: Tasks 11–12 come before this task. The operator removes the old secret files and secret-store entries once the release is healthy.

**Risks:**

- Losing uncommitted code: the archive tag is checked first.
- Captions, voice emotion and remote embeddings are unavailable between Tier 1 and Tier 2: acceptable only with no live users.
- A missed string in generated contracts or scripts: Task 27 catches it.

**Acceptance criteria:** the archive tag exists on the remote; the files and symbols above are gone; Task 17's conformance suite still passes; object detection, diarization and stems work through template routes on fakes; captions, voice emotion and embeddings report `unavailable` without startup errors; B-roll, visual analysis and transcription still complete on fakes.

#### 26. Configuration, Compose, CI and docs cutover

**Dependencies:** 9A, 10, 14, 19.

**Files:**

- **Settings:** `apps/api/app/core/config.py`.
  - Remove the remaining vendor fields (`GROQ_*`, `OPENROUTER_*`, `MODEL_GATEWAY_ROUTES`, `MODEL_GATEWAY_OPENAI_*`, `AZURE_OPENAI_*`), the vendor model defaults for planning and transcription, the vendor provider switches, and the vendor names in the `get_settings()` `_FILE` list.
  - Add `AI_REGISTRY_FILE` and the secrets bundle settings.
  - Delete `apps/api/app/services/model_gateway_factory.py` and `apps/api/app/services/ai_features.py` if Tasks 6 and 8 left wrappers.
- **Env examples:** `.env.example`, `apps/api/.env.example`, `prod.env.example`, `infra/production-compose.env.example`, `infra/compose-host/host.env.example`.
- **Compose:**
  - `infra/docker-compose.gpu.yml`: replace the four vendor secrets with one registry file and one secrets bundle.
  - `infra/docker-compose.remote-ai.yml`: keep the topology only; drop vendor switches, prices and secrets. `demucs-worker` keeps its `local-stems` profile, and the deployment guide explains when to start it: only when the CPU-local Demucs route is configured.
  - `infra/docker-compose.cpu-stage.yml` with **NEW (proposed)** `infra/ai-registry/cpu-stage.json` (fake routes).
- **CI and scripts:**
  - `.github/workflows/live-provider-contracts.yml`: registry-driven, with a generic secrets bundle; manual authorization stays.
  - Lines 282–284 of `scripts/deploy/apply_release.sh`.
  - `scripts/release/write_compose_test_env.py`, `setup-local-env.sh`, `setup-local-env.ps1`, `apps/api/scripts/audit_local_capabilities.py`.
- **Docs:** `/workspaces/Motionly-claude/DEPLOYMENT-GUIDE.md` (Git root), `docs/generated/motionly_technical_documentation.md`, `docs/security/data-handling.md`, `docs/security/secrets.md`, `docs/ops/compose-vm-deployment.md`, `docs/ops/supply-chain.md`. Vendor names may remain there as documented examples.

**Migration:** none.

**Implementation:** Path B deletes the vendor variables; the deployment guide lists them so operators remove them from env files and secret stores. Path A does the same in Release N+2, and deletes Task 6L at the same time.

**Tests:** `apps/api/tests/test_production_compose_contract.py`, `test_remote_ai_compose_contract.py`, `test_compose_empty_environment.py`, `test_release_compose_inputs.py`, `test_static_compose_gpu_retirement.py`, `test_local_audit_capabilities.py`, `test_gpu_free_deployment.py`; the `pnpm verify` stages; `pnpm contracts:check`.

**Risks:** an operator keeps old env files or release scripts that reference removed secret names. The deployment guide checklist covers both.

**Acceptance criteria:** a fresh production Compose deployment with only a registry file and a secrets bundle passes validation and runs planning and transcription, with no GPU service and no vendor-specific environment variable.

#### 27. Enforce the neutrality proof (Tier 1 gate)

**Dependencies:** 2, 9A, 26.

**Files:** the scanner and allowlist from Task 2; `scripts/verify/stages.json`; release evidence.

**Migration:** none.

**Required checks:**

```bash
# Strict scan; only allowlisted adapter files, registry files and docs may match.
python scripts/verify/vendor_neutrality.py --enforce

# No vendor environment variable or secret name in active code, Compose, CI or scripts.
grep -RInE 'RUNWAY|OPENROUTER|GROQ|JINA|GOOGLE_VISION|PYANNOTEAI|LALAL' \
  apps/api/app apps/web apps/worker infra .github scripts \
  --exclude-dir=__pycache__ --exclude='*.lock' --exclude=vendor_neutrality.py

# The old payload keys are not written by current code.
grep -RInE 'runway_task_id|runway_gateway_route|provider="runway"' \
  apps/api/app apps/web packages/shared

# No vendor SDK import.
grep -RInE '^\s*(from|import) (openai|anthropic)\b' apps/api/app

# Capability, gateway and deployment tests.
(cd apps/api && python -m pytest -k 'inference or gateway or capability or gpu_free or compose')
```

Each `grep` must print nothing. The scanner reports historical applied migrations separately because they cannot be rewritten; a match in a new migration fails.

**Tests:** full CI (`pnpm verify`, `pnpm test`, `pnpm build`, `pnpm contracts:check`), the fake suite with network blocked, Supabase migration tests and the no-GPU Compose smoke test.

**Rollout/back-compat:** the scanner stays in `scripts/verify/stages.json`, so the proof re-runs at the Tier 2 and Tier 3 gates.

**Risks:** misleading matches from generated files or fixtures. The scanner prints path, line and reason for every allowlisted match.

**Acceptance criteria:** every check reports zero active-source findings, which completes the Tier 1 gate.

#### Tier 1 gate (MVP)

All of these must pass before Tier 2 starts:

1. **No-GPU boot:** a production-mode deployment boots with `GPU_WORKERS_ENABLED=false`, no GPU service, a registry file with `openai_compatible` routes for `planning.chat` and `transcription.speech_to_text`, and secrets from secret files or a bundle only.
2. **End to end:** upload → transcript → plan works against the fake provider in CI, and once manually against the operator's real remote endpoints. The transcript has word timestamps.
3. **Capabilities:** `GET /capabilities` reports planning and transcription available. CPU-local matte, stems and sound classification are available when **NEW (proposed)** `infra/ai-registry/cpu-local.json` (Task 14) is mounted. Every other capability is `unavailable` with a reason until a route is configured. Controls for unavailable features may still be clickable until Task 9B; the API answers them with `capability_unavailable` (503).
4. **Ported capabilities:** object detection, diarization and stem separation pass Task 17's conformance and differential suites. Each works from registry config alone for two differently shaped fake providers. With no route, each one uses its fallback: empty detections, heuristic speaker labels, no stem control.
5. **Archive and deletion:** the tag `archive/pre-neutral-inference` exists on the remote, and the three vendor adapter files were deleted only after item 4 passed.
6. **Validation:** the Section 2.6 matrix passes, including a `provider-managed` remote route (warning), a local route with `latest` (error), a production remote route without data-processor metadata (error), and unknown-pricing routes with and without a hard cap.
7. **Quotas:** planning quota is feature-keyed (Decision B-1), or `AI_QUOTA_SCOPE=provider` is set deliberately and documented.
8. **Runway removed:** no Runway code, setting, secret, health probe, Compose entry, quota name, CI secret or UI string remains, and migrated generated B-roll rows use the neutral fields.
9. **Neutrality:** `python scripts/verify/vendor_neutrality.py --enforce` reports zero active-source findings (Task 27), and the fake suite passes with network access blocked.

### 4.2 Tier 2 — UI gating, captions, voice emotion and embeddings

#### 9B. Gate the UI on capabilities

**Dependencies:** 8, 18.

**Files:**

- `apps/web/components/editor/ScalePanel.tsx`.
- `apps/web/components/editor/ai-step.tsx`.
- `apps/web/components/editor/tabs/audio-tab.tsx`.
- Existing source-contract tests in the style of `apps/web/tests/task31-capability-screens.test.mjs`, and `apps/web/tests/browser/capability-screens.spec.mjs`.
- **NEW (proposed):** `apps/web/lib/capabilities.ts` and `apps/web/hooks/useCapabilities.ts`.

**Migration:** none.

**Implementation:** fetch capability status and disable unavailable controls with visible text such as `Unavailable — not configured by your operator`. Preserve keyboard and focus behavior and use `aria-disabled`; never signal state by color alone. This covers the Generated B-roll control from Task 18.

**Tests:** missing route, degraded route, available route, keyboard accessibility, no secret or model URL in the client payload.

**Rollout/back-compat:** the existing degraded visual and transcript behavior stays.

**Risks:** a stale client capability cache. Include the registry version, and refresh after a change.

**Acceptance criteria:** disabling a route changes the corresponding control to a clear unavailable state without breaking unrelated editing, and no vendor-specific provider name is needed in the UI.

#### 11. Captions and voice emotion on generic chat routes

**Dependencies:** 6, 8, 9A, 19.

**Files:**

- `apps/api/app/services/remote_visual.py`: an enricher is active when its capability has a route. This replaces the vendor switches removed in Task 19.
- `apps/api/app/services/remote_audio_emotion.py`: the neutral strategy kept in Task 19.
- `apps/api/app/main.py`.
- **NEW (proposed):** `apps/api/app/services/inference_strategies.py`.
- Tests `apps/api/tests/test_remote_visual_captions.py`, `test_remote_audio_emotion.py` and `test_remote_visual_e2e.py`, rewritten on fake routes.

**Migration:** none.

**Implementation:** Motionly owns the prompt, response schema and parsing. Any `openai_compatible` route for `vision.caption`, or for `audio.emotion` with an audio-input model, can serve it. Zero-retention or other provider routing preferences are adapter `extra_body` data, not code.

**Tests:** fake chat and vision provider; malformed structured response; partial caption failure; degraded manifest when unconfigured.

**Rollout/back-compat:** Path B: operators add routes, and caches keyed on the route catalog are recomputed. Path A: this task lands before Task 19, and Task 6L keeps the old route ids.

**Risks:**

- Prompt or parsing regressions: port the existing prompt fixtures first.
- With `provider-managed` revisions, cached captions cannot detect a provider model change; this is covered by the warning.

**Acceptance criteria:** captions and voice emotion work with any configured compatible route, and are reported degraded, not fatal, when no route exists.

#### 12. Text and image embeddings on configured routes

**Dependencies:** 6, 8, 9A, 14, 19.

**Files:**

- `apps/api/app/services/model_adapters.py`: add `embeddings_input_style: openai|typed_objects` to `OpenAICompatibleAdapter`. Port the typed-objects request shape from the vendor adapter archived in Task 19.
- `apps/api/app/services/model_domain_adapters.py` (`GatewayClipEmbeddingProvider`).
- `apps/api/app/services/remote_visual.py`, `apps/api/app/services/stock_video.py`, `apps/api/app/services/layer_jobs.py`, `apps/api/app/api/broll.py`.
- B-roll and visual tests.

**Migration:** none. Re-embed when `embedding_space` or the revision changes, using the existing cache-version logic.

**Implementation:** the text and image routes used by one consumer must declare the same `embedding_space`. The lexical fallback stays.

**Tests:** dimensions, input order, image MIME check, normalization and cache invalidation; switching routes never mixes vector spaces.

**Rollout/back-compat:** Path B: stock-asset embeddings are recomputed for the new route. Path A: Task 6L keeps the old route identity.

**Risks:**

- Mixed vector spaces: validation rejects mismatched spaces.
- A `provider-managed` embeddings route can change vectors silently: the warning recommends pinning embeddings routes.

**Acceptance criteria:** text and image embeddings are added through route config only. With no route, B-roll uses lexical ranking and `/capabilities` reports `unavailable`.

#### Tier 2 gate

1. Every optional-capability control in the editor shows a clear, keyboard-accessible unavailable state driven by `/capabilities` (Task 9B).
2. Captions, voice emotion, and text and image embeddings work through any configured `openai_compatible` route, and degrade instead of failing when unconfigured.
3. `AI_QUOTA_SCOPE` is removed if one full billing period has passed since Tier 1 shipped; otherwise its removal date is recorded.
4. The neutrality scan still reports zero findings.

### 4.3 Tier 3 — generic adapters, database registry, admin tooling and more protocols

#### 28. `generic_http` and `async_job` — Tier 3 extensions

**Dependencies:** 15, 16B.

**Files:**

- **NEW (proposed), created in Tier 1:** `apps/api/app/services/generic_http_adapter.py`, `apps/api/app/services/async_job_adapter.py`, `apps/api/app/services/inference_templates.py`.
- Queue bridge files under `apps/worker/src/` (for example `queue-bridge.mjs`), only if delayed jobs are not already supported.

**Migration:** none.

**Implementation:** exactly the Tier 3 extension list in Section 2.10:

- multipart request bodies;
- `xyxy_norm` and `xyxy_px` box formats;
- `usage` taken from a response path;
- non-blocking async mode, which re-enqueues delayed polls so long video jobs do not hold a worker;
- typed mappings for synchronous transcription, captions and embeddings on APIs that are not OpenAI-compatible.

**Tests:**

- Each extension.
- A long async video job in non-blocking mode resumes after a worker restart without resubmitting.
- A config-only transcription route on a non-compatible fake API returns word timestamps.

**Rollout/back-compat:** additive. Tier 1 routes keep working unchanged.

**Risks:** grammar creep. Anything outside the Section 2.10 list needs a plan change.

**Acceptance criteria:** each extension works from registry config only, against fakes, and the Tier 1 conformance suite still passes.

#### 13. Deleted

The transitional adapter is removed from the plan. Path B has nothing to keep running during a transition, and Task 17 ports the three integrations directly to templates.

#### 20. DB registry and AES-256-GCM encrypted global secrets

**Dependencies:** 4, 5, 7, 16A.

**Migration:** **NEW (proposed):** `supabase/migrations/<timestamp>_inference_registry.sql` and `supabase/tests/inference_registry_test.sql`.

Tables:

- `ai_adapters`: id, type, base URL, auth metadata, network policy, health/mapping config, data-processor metadata, enabled, timestamps.
- `ai_models`: model id, external id, revision/SHA, licence, commercial-use flag, source/source URL, capabilities, minimum VRAM, weights SHA, verification metadata. Licence and commercial use are nullable, because remote models may omit them.
- `ai_routes`: route id, capability, adapter/model references, revision, `allow_provider_managed_revision`, priority/fallback, strategy, pricing (including `pricing_status`), budget and `hard_cap`, limits, compute requirement, scope, enabled, timestamps.
- `ai_secrets`: scope/name, ciphertext, nonce, key version, fingerprint, creator, and rotation/use timestamps; no plaintext column.
- `ai_registry_version`: a singleton version counter updated in the same transaction as each registry write.

Apply RLS, service-role access and restricted worker grants, following the existing least-privilege migration patterns. Workspace scope columns exist, but a feature flag defaults to false, and validation rejects non-global entries while it is off.

**Files:**

- `apps/api/app/core/config.py`.
- **NEW (proposed):** `apps/api/app/services/inference_registry_store.py`.
- `inference_secrets.py` and `inference_runtime.py` from Tasks 5 and 6.

**Implementation:** the Task 4/7 validator runs on every write and every load. The local-model licence rule, the revision policy, the production data-processor rule and the hard-cap rule therefore apply to database entries exactly as they do to the file.

**Tests:**

- Encryption round-trip, wrong-AAD rejection and KEK rotation.
- No plaintext in API or database logs.
- RLS and worker permissions.
- Atomic version bump, and reload without disrupting active calls.
- Global-scope enforcement.
- Policy rules on writes: a local model without licence, `provider-managed` without the flag, and unknown pricing without a cap are each rejected.

**Rollout/back-compat:** the file registry remains a complete fallback. The database registry is additive; no existing secret is migrated until the operator explicitly imports it.

**Risks:** losing the KEK loses every database secret. Document secure backup, rotation and a break-glass procedure before enabling database secrets.

**Acceptance criteria:** an admin-created global secret and route become active after a registry reload, without an API restart, and the plaintext secret is never returned or logged.

#### 21. Add admin inference registry API

**Dependencies:** 16A, 20.

**Files:**

- `apps/api/app/api/admin.py`, or a new router included from it.
- **NEW (proposed):** `apps/api/app/api/admin_inference.py` and `apps/api/app/schemas/admin_inference.py`.
- `apps/api/app/services/admin_user_directory.py`, for the existing audit-log conventions.
- `apps/api/tests/test_admin.py`, `test_role_route_inventory.py`.
- Shared OpenAPI output.

**Migration:** none beyond Task 20.

**Implementation:**

- CRUD and dry-run for adapters, models and routes.
- Write-only secret create, rotate and delete; the secret list returns metadata only.
- Route deletion is refused while non-terminal operations reference the route.
- Every change is audited, and the registry is validated before commit.
- Responses include validation warnings: `provider-managed` revision, unknown pricing with cap usage, missing remote licence, and missing data-processor metadata in dev.

**Tests:** every endpoint; admin authorization and non-admin 403; no secret in any response or log; duplicate ids; invalid routes; RLS and service boundaries; rate limiting.

**Rollout/back-compat:** admin-only, and disabled until the database registry is verified. File entries are shown as managed and read-only.

**Risks:** request-body logging and privilege escalation. Confirm middleware redaction and use `require_admin` on every route.

**Acceptance criteria:** an operator can create a global secret, adapter, model and route through the API, sees only secret metadata afterwards, and can activate the route through a reload.

#### 22. Add route test action and per-route health/circuit status

**Dependencies:** 8, 20, 21.

**Files:**

- `apps/api/app/services/model_gateway.py`: a read API for circuit state.
- **NEW (proposed):** `apps/api/app/services/inference_route_test.py` and sample fixtures.
- Admin inference router and schemas.
- `provider_slo_events` query/repository code.
- `apps/api/tests/test_model_gateway.py`, `test_metrics_telemetry.py`, new admin inference tests.

**Migration:** none expected. `provider_slo_events` already has a `(route_id, model_revision, created_at desc)` index from `202607140010_provider_neutral_model_gateway.sql`.

**Implementation:**

- Tiny typed samples per capability, with a per-test cost limit and an operator budget scope.
- Paid async submits need explicit confirmation.
- Results report schema validity, latency, usage, estimated and actual cost, error code and circuit state.
- Tests of hard-capped routes count against the cap.
- Add `CircuitStore.state()` to the Redis and in-memory stores.

**Tests:** success, invalid schema, timeout, auth failure, cost cap, hard-cap accounting, circuit opening and closing, paid async confirmation, and a route-affine status test.

**Rollout/back-compat:** admin-only. No automatic health probe can submit a paid async job.

**Risks:** accidental spend or sensitive sample data. Use synthetic samples, and require explicit `allow_paid` for submits.

**Acceptance criteria:** the operator can test a configured route and see latency, cost and schema status without seeing a secret; a malformed response is reported as `schema_invalid`.

#### 23. Add admin AI configuration UI

**Dependencies:** 9B, 21, 22.

**Files:**

- **NEW (proposed):** `apps/web/app/(app)/admin/ai/page.tsx` and `actions.ts`.
- Existing `apps/web/app/(app)/admin/ops/` patterns and `apps/web/components/app-shell.tsx`.
- Admin source-contract and browser tests.

**Migration:** none.

**Implementation:**

- Forms for adapters, models and routes, with write-only secret fields.
- Route priority and fallback ordering, usable by keyboard.
- Capability matrix, health and circuit panel, and the test action.
- Warning badges from Section 2.7.
- Explicit confirmation before deletes.
- Admin auth is enforced server-side, and no secret value is held in browser state.

**Tests:** accessibility; admin-only navigation and API; the add/test/disable flow; no secret in rendered source or network responses; unavailable feature state after a route is disabled.

**Rollout/back-compat:** admin-only and additive; existing admin pages are unchanged.

**Risks:** exposing internal endpoints or secret fingerprints too broadly. Return only metadata and protect every mutation server-side.

**Acceptance criteria:** an operator can add a key, model and route, test it, reload, and see the editor feature become available without a code change or image rebuild.

#### 24. Add Hugging Face endpoint adapter

**Dependencies:** 7, 22.

**Files:** **NEW (proposed)** adapter module under the existing API service tree; registry examples; adapter tests.

**Migration:** none.

**Implementation:** endpoint modes for text generation, vision and audio, text embeddings, and pipeline responses, each through a fixed typed mapping in the adapter. Bearer secret refs only; no Hugging Face names in core.

**Tests:** a fake endpoint for each supported mode; response normalization; timeout, auth and error classification; the route test.

**Rollout/back-compat:** additive optional adapter.

**Risks:** endpoint responses vary. Each capability mapping must be explicit and schema-validated.

**Acceptance criteria:** a registry-only Hugging Face route passes the admin route test and serves a feature call through fake contract fixtures.

#### 25. Add AWS Bedrock adapter with IAM/SigV4

**Dependencies:** 5, 7, 22.

**Files:**

- **NEW (proposed):** adapter module and SigV4 auth strategy.
- `apps/api/pyproject.toml`, only if an exactly pinned optional `botocore` dependency is approved.
- AWS adapter tests.

**Migration:** none.

**Implementation:** Converse and InvokeModel mappings are adapter or config data. Use the IAM credential chain and SigV4; the IAM path stores no static cloud key as an AI secret.

**Tests:** AWS SigV4 published test vectors, a fake Bedrock transport, credential refresh, auth failure and typed schema validation.

**Rollout/back-compat:** optional adapter; the no-GPU baseline needs no AWS route.

**Risks:** dependency size and request-shape differences. Keep the adapter optional and its protocol tests exhaustive.

**Acceptance criteria:** a route using an IAM role passes the fake Bedrock contract and the route validator without a static API key.

#### Tier 3 gate

1. An admin can add a global secret, adapter, model and route at runtime, test it, and see the feature become available without a restart. Secrets never appear in responses or logs.
2. The Task 28 extensions pass their grammar tests. A config-only long async video route works in non-blocking mode against fakes, and an after-send timeout never reaches a second route.
3. Hugging Face and Bedrock routes pass the route test against fakes.
4. Task 17's conformance suite still passes.
5. The neutrality scan still reports zero findings, with allowlist entries for the new adapters.

---

## 5. Migration inventory and ordering

All migrations are forward-only. Each one has a matching file in `supabase/tests/`, as `apps/api/tests/test_migration_contract_coverage.py` requires. Never edit an applied migration.

| # | Tier / task | Migration | Path B (default) | Path A |
|---|---|---|---|---|
| M1 | Tier 1 / 10 | Index `cost_attribution_events(workspace_id, category, created_at desc)` | Same | Same |
| M2 | Tier 1 / 16A | `inference_operations` table, RLS and worker grants | Same | Same |
| M3 | Tier 1 / 18 | Add `generated` to `public.stock_provider` (its own migration) | Same | Same |
| M4 | Tier 1 / 18 | Backfill providers and operation rows; replace old payload keys; `CHECK (provider <> 'runway')` | One-time rewrite; constraints added immediately | Backfill in Release N while keeping the old keys for dual-read; constraints in N+1 |
| M5 | Tier 3 / 20 | Registry tables, encrypted secrets, version counter | Same | Same |

- Tier 2 has no migrations, and Task 22 needs none because the existing `provider_slo_events` index covers it.
- Take a database backup before M4 (procedure in `docs/backups-restore.md`).
- Historical `cost_attribution_events` rows are never rewritten.
- The old enum value stays physically, but code no longer writes it.

---

## 6. Rollout runbook

### Tier 1 release (Path B: single cutover)

1. Confirm the no-live-users assumption.
2. Create and push the archive tag `archive/pre-neutral-inference` with the untracked and uncommitted inference code (Task 17, Step 0).
3. Write the production registry file and the secrets bundle. The registry needs:
   - planning and transcription routes with pricing, revision policy and data-processor metadata;
   - `cpu-local.json` if you want the CPU-local features;
   - routes copied from **NEW (proposed)** `docs/ai-registry/examples/` (Task 17) for object detection, diarization and remote stems, if you want them.
4. Back up the database, then apply M1–M4.
5. Deploy the new images with the registry and bundle, without any GPU profile.
6. Check the Tier 1 gate.
7. Remove the old vendor secret files from `SECRETS_DIR`, and the secret-store entries that `scripts/deploy/apply_release.sh` used to fetch.

Rollback: restore the backup and redeploy the previous images. Path B has no compatibility layer to roll back onto.

### Tier 2 release

Additive. Operators add caption, emotion and embedding routes, each with data-processor metadata. Remove `AI_QUOTA_SCOPE` once a full billing period has passed since Tier 1. Rollback: disable the routes.

### Tier 3 releases

Additive, and may ship in parts:

- Back up the KEK before enabling database secrets (Task 20).
- Enable the admin API and UI only after the registry tables are verified.

Rollback: disable the database routes; the file registry keeps working.

### Path A (only if live users or data exist)

- **Release N:** Tier 1 code plus Task 6L. Old variables and payload keys still work. Apply M1–M3, and run the M4 backfill without dropping keys.
- **Release N+1:** production runs on the registry, with dual-read on. Add the M4 constraints.
- **Release N+2:** delete Task 6L, the old settings and dual-read. Task 26 completes here.
- **Ordering:** Tasks 11–12 come before Task 19. Task 17 already does in every path.

### Rules for every path

- Never roll back by dropping `inference_operations`, registry tables or enum values.
- Never resubmit an uncertain async submit; reconcile `submit_unknown` by polling the original route.
- Revoke old secrets only after every service runs the new configuration.

---

## 7. Required test matrix

### Tier 1

- **Schema:** request and response types for every capability; registry format versioning.
- **Validation (Section 2.6):**
  - revision policy by locality: `provider-managed` with and without the flag; `latest` on local routes;
  - licence: required for local routes, a warning for remote routes;
  - data-processor metadata: a warning in dev, an error in production;
  - pricing: priced, free, and unknown with and without a hard cap, including cap exhaustion;
  - fake adapters and GPU routes in production.
- **Gateway:**
  - priority and fallback;
  - no fallback after send;
  - status calls bound to the route snapshot;
  - budgets;
  - feature-keyed quota continuity across provider changes, in both `AI_QUOTA_SCOPE` modes;
  - circuits.
- **Adapters:** `openai_compatible` chat and transcription with word timestamps; `local` matte, Demucs and YAMNet; the fake adapter with network blocked.
- **Secrets:** file and bundle references; redaction in logs, errors and responses.
- **Data:** M1–M4 forward application, RLS, backfill idempotency and constraints; old generated B-roll still renders.
- **Templates (Tasks 15, 16B):** every allowed value form and function in the Tier 1 slice; rejection of every disallowed construct; `auth: none` steps; captured-URL SSRF checks; artifact limits; cleanup and temp-directory removal; no resubmit after commit.
- **Ported capabilities (Task 17):** the conformance suite and the differential suite against the archived adapters for object detection, diarization and stems, covering every row of the parity table; two differently shaped fake providers per capability; the no-route fallbacks (empty detections, heuristic speaker labels, stem request refused with `capability_unavailable`).
- **Removal:** missing caption, emotion and embedding routes degrade without failing jobs, and those capabilities report `unavailable`.
- **Deployment:** a no-GPU Compose deployment with only the registry and bundle; Compose contract tests; `/capabilities` and `/public/data-processors` contracts.
- **Neutrality:** scanner enforce mode.

### Tier 2

- UI unavailable states, including keyboard and screen-reader behavior (source and browser tests).
- Captions and emotion on fake chat routes, including malformed and partial responses.
- Embeddings with both input styles; mismatched embedding spaces rejected; lexical fallback.

### Tier 3

- **Database registry:** encryption round-trip, AAD, KEK rotation, RLS, policy rules on writes, reload during active calls, and workspace scope rejected while disabled.
- **Admin API and UI:** authorization, no secret in any response or page, warnings shown.
- **Route test:** success, `schema_invalid`, timeout, cost cap, hard-cap accounting, paid async confirmation.
- **Task 28 extensions:** multipart, extra box formats, usage paths, non-blocking async mode (resume after worker restart without resubmit), and typed mappings for non-compatible transcription, caption and embedding APIs.
- **Hugging Face and Bedrock:** fakes, plus SigV4 test vectors for Bedrock.
- **Regression:** Task 17's conformance suite still passes.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Duplicate paid async submissions | Persist the operation before send; idempotency key; commit boundary; `submit_unknown`; polling on the original route; no fallback after send. |
| Provider switch resets quota | Decision B-1 counts by feature; `AI_QUOTA_SCOPE=provider` is only a temporary stop-gap. |
| Secret leakage through logs or UI | Secret references only; write-only endpoints; registration of exact values with the redactor; no request-body logging; fake-secret tests; AES-GCM for database secrets. |
| Admin-configured URL SSRF | HTTPS and public-host validation; explicit private-network flag; IP and metadata-address blocking; artifact host allowlist; no template host override. |
| Generic templates become a programming language | The capped grammar in Section 2.10; any extension needs a plan change. |
| In-flight route removed or edited | Immutable route snapshot; deletion refused while non-terminal operations exist; status calls use the snapshot. |
| GPU-free boot accidentally requires a GPU | Route `requires_gpu`; capability startup validation; Compose contract test. |
| Local model licence violation | Required licence and commercial-use flags with source URL and verification timestamp; optional block flag. |
| Provider changes behavior or vectors under a `provider-managed` revision | Explicit opt-in and warning; `provider-managed` in cache keys; the reported model id in telemetry; pinning recommended for embeddings routes. |
| Unknown-pricing route overspends | Hard cap in requests or units; `cost_unknown` telemetry; startup warning. |
| Worker/API registry drift | Registry version and route catalog fingerprint in startup and heartbeat checks. |
| Deleting untracked or uncommitted code loses it | Archive tag `archive/pre-neutral-inference` (Task 17, Step 0); Task 19 checks the tag before deleting. |
| A ported capability behaves differently from the archived adapter | The parity table in Task 17, with a conformance scenario and a differential scenario per row; deletion is blocked until both pass. |
| Tier 1 grows by three tasks | Accepted, because Task 27 requires the vendor files gone and Task 19 may delete them only after Task 17 passes; Task 28 holds everything beyond the minimum slice. |
| Captions, voice emotion and remote embeddings unavailable between tiers | Accepted under Path B; Path A puts Tasks 11–12 before Task 19. |
| Historical migration grep noise | The immutable migration report is separate from the strict active-source proof; new coupling is never hidden in that exception. |
| Backfill races with old writers | Path A deploys neutral readers and writers first, then backfills, then removes old writers. |
| Overly broad inference scope | Unrelated stock, payment, storage and rendering features stay out of this change, except for their existing inference references. |

---

## 9. Final neutrality proof

These are checked at every tier gate; the full list applies after Tier 3. The tier in parentheses is where each item is first met.

1. `planning.chat` and `transcription.speech_to_text` are the only startup-required capabilities. (Tier 1)
2. Every optional capability appears in `/capabilities` with a reason when unavailable (Tier 1), and the UI shows a clear unavailable state (Tier 2).
3. Adapters, routes, models, revisions, prices, caps, budgets, auth and mappings are configuration: the file in Tier 1, plus the database in Tier 3.
4. Secrets are referenced by name and never returned or logged (Tier 1). Database secrets are encrypted with AES-256-GCM (Tier 3).
5. Async operations persist `provider_operation_id`, `provider_route_id` and `provider_model_revision`, and never resubmit after the send point. (Blocking mode: Tier 1; non-blocking mode: Tier 3.)
6. A no-GPU deployment runs on remote and CPU routes, and a later private compatible endpoint needs only configuration. (Tier 1)
7. Object detection, diarization and stem separation work with any provider from registry config only, with behavior parity to the archived adapters, and fall back as documented when no route is configured. (Tier 1)
8. Runway, Google Vision, Jina, OpenRouter, Groq, pyannoteAI and LALAL code, settings, secrets, health checks and UI strings are removed, or confined to adapter implementations, registry files and docs. No Anthropic or OpenAI SDK is imported. (Tier 1)
9. The fake suite passes with no real keys and network access blocked. (Every tier)
10. The active-source neutrality scan reports zero findings outside allowlisted adapter implementations, registry files and docs. Historical migrations are reported separately. (Every tier)

Release gate commands, run from the repository root:

```bash
python scripts/verify/vendor_neutrality.py --enforce
(cd apps/api && python -m pytest)
pnpm verify
pnpm test
pnpm build
pnpm contracts:check
# plus the Supabase migration tests and the no-GPU Compose smoke test
```

Retain the scanner output, migration output, route catalog, `/capabilities` response and no-GPU smoke result as release evidence for each tier.

---

## Blocking questions

1. **Live users or data?** Earlier you answered `1a` (preserve existing deployments), but this revision defaults to no live production users or data and plans Path B, a single clean cutover. Which applies? Path A adds Task 6L, dual-read and three releases, and moves Tasks 11–12 before Task 19.
