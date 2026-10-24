# Motionly Provider-Neutral Inference Implementation Plan

**Status:** planning only; no application code is changed by this document.

**Repository audited:** `motionly-main/Motionly-main`

**Decisions recorded from operator:**

- **Existing data/deployments:** preserve them with a one-release compatibility bridge (`1a`).
- **Existing object detection, diarization and stem integrations:** convert them to declarative `generic_http`/`async_job` templates and delete their provider-specific implementation code (`2b`).
- **Secret storage and key scope:** global operator keys now, encrypted in Postgres with AES-256-GCM and a deployment KEK; schema is prepared for workspace-scoped BYOK later, but workspace keys are disabled initially (`3a`).

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
7. Model revisions are immutable and pinned. `latest`, floating aliases and an unqualified model id are invalid for production routes.
8. A paid route must declare pricing. A zero-priced route must explicitly declare `explicitly_free: true`; private self-hosted free routes are allowed.
9. CI uses fake adapters and blocks real network access for normal tests. Real provider contracts remain manually authorized and configuration-driven.
10. No GPU is required for the baseline deployment.

### 0.3 Path notation

Paths in the audit tables are files that were actually found in the repository. Paths prefixed **NEW (proposed)** are implementation targets, not claims about files that already exist. Historical migrations are immutable records; new migrations must never add provider-specific schema names.

### 0.4 Compatibility posture

The migration is staged:

- Release N introduces the registry, typed capabilities, secret references and a legacy environment translator.
- Release N keeps reading existing vendor-specific environment variables and old JSONB payload keys, but emits deprecation warnings.
- Release N+1 uses only the registry and generic secrets bundle; old variables fail with a conversion message.
- Release N+2 removes the translator and old settings from the application.

Existing generated media, costs, cached embeddings, in-flight jobs and completed workflows must remain readable throughout the transition.

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
| `apps/api/app/services/model_gateway_factory.py` | `OPENROUTER_BASE_URL`, `GROQ_BASE_URL`, `_HTTP_ADAPTERS = {"azure", "openai_compatible", "openrouter", "groq"}`, vendor-key construction and `_legacy_routes()` create OpenRouter planning and Groq transcription routes. | Build adapters from registry records `{id, type, base_url, auth, secret_ref}`. Keep a one-release translator in a compatibility module. Endpoint host comes from the configured URL, not a provider name. |
| `apps/api/app/services/model_adapters.py` | `OpenAICompatibleAdapter` is reusable, but `JinaEmbeddingsAdapter` and `JINA_BASE_URL` hard-code a provider-specific request shape. `AzureOpenAIAdapter` uses Azure-specific transport, which is acceptable only inside the Azure adapter implementation. | Keep protocol adapters under an adapter boundary. Express typed embedding input style and mapping in route config; delete the dedicated Jina adapter after conformance tests pass. |
| `apps/api/app/services/model_domain_adapters.py` | Contains `RunwayVideoGenerationAdapter`, Jina registration, OpenRouter ZDR registration, Google Vision registration, pyannoteAI registration, LALAL registration and vendor-named route ids/provider strings. | Capability routes come from the registry. Existing special integrations become temporary templates and are then deleted. Local runners remain behind a neutral local adapter. |
| `apps/api/app/services/model_gateway.py` | Core is mostly neutral but uses coarse capability families and old operation names (`vision.prompt`, `audio.prompt`, `generation.video_submit`, etc.). Comments contain provider examples. | Preserve the gateway engine; add the fine-grained capability taxonomy and compatibility aliases. Move provider examples into docs. |
| `apps/api/app/services/model_gateway_factory.py`, `apps/api/app/main.py`, `apps/api/app/workers/visual_job.py`, `apps/api/app/workers/matte_job.py` | API and workers construct gateways from environment-specific vendor switches. Workers construct their own gateway in separate processes. | One registry/runtime builder used by API and workers; registry version and route fingerprints keep them aligned. |
| `apps/api/app/services/model_provider_bridges.py` | `GatewayEditPlannerProvider` subclasses the direct HTTP planner and carries endpoint/key compatibility details. | Make the bridge accept only a gateway and typed planning request. |
| `apps/api/app/services/edit_planner.py` | Direct HTTP planner remains and calls `check_quota(provider="openrouter", category="tokens")`. | Gateway-only planning. Quota is keyed by Motionly feature/category, not route provider. |
| `apps/api/app/services/transcription_provider.py`, `apps/api/app/main.py` | Non-production fallback uses `GROQ_TRANSCRIPTION_ENDPOINT` and Groq credentials directly. | Gateway-only transcription with fake route in CI/dev. |
| `apps/api/app/services/remote_visual.py` | Caption, object and image-embedding enrichers call vendor-specific route registration helpers and vendor-specific route ids. | Enrichers request `vision.caption`, `vision.object_detection` and `embeddings.image`; batch limits and mappings belong to route metadata. |
| `apps/api/app/services/google_vision.py` | Provider-specific object-localization HTTP adapter, `GOOGLE_VISION_*`, label aliases and vertex-to-box conversion. | Convert behavior to a documented `generic_http` template plus Motionly-owned normalization and conformance fixtures; delete the implementation after parity. |
| `apps/api/app/services/pyannoteai_diarization.py` | Provider-specific upload reservation, signed upload, async submit/poll, 20-second minimum billing and seconds-to-milliseconds parsing. | Convert to `async_job` declarative steps with `commit: true`, per-step auth, polling state map, route pricing minimum and generic audio upload transform. Delete implementation after parity. |
| `apps/api/app/services/lalal_stems.py` | Provider-specific FLAC conversion, upload, split submit, POST polling, artifact download limits/host checks and cleanup. | Convert to `async_job` template with `input_transform.audio`, artifact policy, cleanup/finally steps, idempotency and route-affine state. Delete implementation after parity. |
| `apps/api/app/services/remote_audio_emotion.py` | OpenRouter-specific class name and route setup although prompt/schema behavior is a generic audio chat strategy. | Move to a Motionly-owned audio-emotion strategy using any route that supports `audio.emotion`. |
| `apps/api/app/services/scale.py` | `RunwayGen3Provider`, `FixtureRunwayProvider`, `RunwayResult`, `runway_task_id`, `runway_gateway_route`, Runway cost/quotas/errors, provider-specific download names. | Generic video-generation operation records, neutral `generated` asset kind and `provider_operation_id`/`provider_route_id`/`provider_model_revision`. |
| `apps/api/app/api/scale.py` | Endpoint `generate_runway_broll`, Runway premium copy, `runway_not_configured`. | Preserve URL compatibility for one release if needed, but expose neutral operation naming and `capability_unavailable`. |
| `apps/api/app/services/job_engine.py` | Passes `runway_provider` and `runway_gateway_route` through job execution. | Pass a generic inference runtime/operation service. |
| `apps/api/app/services/billing.py` | `PROVIDER_QUOTAS` keys `runway` and `openrouter`; `PlanFeatureQuotaGuard` counts by `route.provider`, so changing providers can reset a workspace's effective feature quota. | Keep the guard but key it by Motionly feature/category. Provider label remains telemetry metadata only. |
| `apps/api/app/services/model_gateway_factory.py`, `apps/api/app/core/config.py`, `apps/api/app/core/production.py` | Vendor-specific route defaults, provider allowlists, required keys, pricing checks and pinned revision maps. Production currently hard-requires Runway generation in addition to planning and transcription. | Validate enabled routes by capability: planning and STT required; all others optional. Validate auth, pinned revision, pricing/free declaration and GPU suitability generically. |
| `apps/api/app/api/health.py` | `_probe_providers()` hard-codes OpenRouter, Groq and Runway URLs/headers. | Probe each configured adapter's declared health request and expose route/circuit health without provider names. |
| `apps/api/app/core/logging.py`, `apps/worker/src/log-redaction.mjs` | Prefix-specific redaction for Groq and OpenRouter keys and a generic `sk-` rule. | Register resolved secret values with a generic redactor and retain only protocol-neutral token patterns. |
| `apps/api/scripts/audit_local_capabilities.py` | Hard-coded vendor key names in local capability auditing. | Audit secret references and route declarations from the registry. |
| `apps/api/app/services/ai_features.py` | Maps feature switches to vendor credentials and provider names. | Replace with a registry-derived capability report. |
| `apps/api/app/core/config.py` | Settings include `GROQ_*`, `OPENROUTER_*`, `RUNWAY_*`, `JINA_*`, `GOOGLE_VISION_*`, `PYANNOTEAI_*`, `LALAL_*`, Azure-specific keys and vendor default model ids. | Keep only generic registry/secrets/budget settings after the compatibility window. |
| `apps/api/app/core/production.py` | Requires `GROQ_API_KEY`, `OPENROUTER_API_KEY`, Runway key/cost and vendor-specific optional credentials; checks vendor-specific provider enum values. | Replace with one capability-based registry validator. |
| `infra/docker-compose.gpu.yml` | Mounts `groq_api_key`, `openrouter_api_key`, `runway_api_key`, `azure_openai_api_key` and environment `_FILE` variables. | Mount one registry/config and generic secrets bundle/KEK. |
| `infra/docker-compose.remote-ai.yml` | Remote mode defaults and prices name Google Vision, OpenRouter, Jina, pyannoteAI and LALAL; mounts their secret files. | Keep topology and CPU/GPU placement only. Routes and prices move into registry config. |
| `.github/workflows/live-provider-contracts.yml` | Passes `OPENROUTER_API_KEY`, `GROQ_API_KEY` and `RUNWAY_API_KEY` directly into a vendor-specific test. | Run registry-selected contract scenarios using a generic secrets bundle and explicit manual authorization. |
| `scripts/deploy/apply_release.sh` | Fetches provider-specific secret-store names at lines 282–284. | Fetch generic registry KEK/bundle references. |
| `scripts/release/write_compose_test_env.py`, `setup-local-env.sh`, `setup-local-env.ps1`, `.env.example`, `apps/api/.env.example`, `apps/worker/.env.example`, `prod.env.example`, `infra/production-compose.env.example`, `infra/compose-host/host.env.example` | Document or generate vendor-specific variables and secret files. | Replace with generic registry and secrets settings, while the compatibility release documents conversion. |
| `apps/web/components/editor/ScalePanel.tsx` | UI state, section title and toast say `Runway Gen-3 B-roll`. | Rename to Generated B-roll and gate it on `video.generation.submit`. |
| `apps/web/app/privacy/page.tsx` | Hard-coded processor copy for OpenRouter, Jina AI and pyannoteAI. | Render configured adapter data-processor metadata. |
| `apps/api/app/schemas/broll.py`, `apps/api/app/repositories/broll.py`, `packages/shared/src/index.ts`, `packages/shared/src/generated/api.ts`, `packages/shared/openapi.json` | `stock_provider` includes/queries `runway`; generated API operation is Runway-named. | Add neutral `generated` and regenerate contracts. |
| `supabase/migrations/202606210003_phase_5_scale.sql` | Adds `runway` to `public.stock_provider`. | Add `generated` in a forward migration and stop writing `runway`; old enum text remains only as historical data until backfilled. |
| `phase5_resources.payload` JSONB | Generated B-roll writes `runway_task_id`, route/model metadata and submit state. | Introduce `inference_operations` with `provider_operation_id`, `provider_route_id`, `provider_model_revision`, and dual-read old payload keys for one release. |
| `stock_assets.provider_asset_id`, `cost_attribution_events.metadata.task_id` | Store provider task id and vendor label indirectly. | Store neutral operation id/route metadata; keep old metadata readable during migration. |
| `apps/api/tests/fixtures/providers/runway_succeeded.json`, Runway tests in `apps/api/tests/` and vendor contract workflow | Tests assert vendor-specific transport and credentials. | Port to fake async adapter and declarative example conformance fixtures. |
| `docs/generated/motionly_technical_documentation.md`, `DEPLOYMENT-GUIDE.md`, `docs/security/data-handling.md`, `docs/security/secrets.md`, `docs/ops/compose-vm-deployment.md`, `docs/ops/supply-chain.md` | Vendor-specific operational and privacy documentation. | Rewrite operator guidance to registry concepts; vendor names may remain only as documented adapter examples. |

### 1.3 Specific requested audit answers

**SDK calls.** No Anthropic or OpenAI SDK call was found. `apps/api/pyproject.toml` has no such SDK dependency. Current outbound inference calls are raw `httpx` implementations. Strings such as `openai/gpt-4.1-mini` and `openai/clip-vit-base-patch32` are model ids or protocol naming, not SDK calls; they must still be moved out of core defaults.

**Production validation.** The main coupling is `apps/api/app/core/production.py`: it validates vendor-specific provider values and keys, requires Runway generation, and includes vendor-specific pricing and revision checks. This is replaced with route/capability validation.

**Health checks.** `apps/api/app/api/health.py::_probe_providers` is vendor-specific. The replacement uses each adapter's declared health specification and reports route/circuit state through capability-neutral names.

**Pricing.** Pricing is spread across `core/config.py`, `model_gateway_factory.py`, `model_domain_adapters.py`, `billing.py`, `services/scale.py` and `infra/docker-compose.remote-ai.yml`. It becomes route-level pricing, with feature quota categories separate from provider identity.

**Runway persisted state.** There is no `runway_task_id` SQL column. It currently appears in `phase5_resources.payload` JSONB in `services/scale.py`. The provider label also appears in the `public.stock_provider` enum, `stock_assets`/`broll_slots`, cost-attribution provider values and generated UI/API contracts. The plan explicitly migrates all of these.

### 1.4 Existing gateway inventory and reuse decision

| Existing component | Actual location | Decision |
|---|---|---|
| Typed requests/outputs (`PlanningRequest`, `TranscriptionRequest`, `VisionRequest`, `EmbeddingsRequest`, generation, matte, stems, diarization, object detection) | `apps/api/app/services/model_gateway.py` | Keep and split/rename into the fine-grained taxonomy; re-export old names during compatibility. |
| `ModelRoute` with id, capability, adapter, model, revision, priority, pricing, `explicitly_free`, enabled state, endpoint host and budget feature | `model_gateway.py` | Keep as the execution engine model; extend with timeout, GPU/CPU requirement, limits, strategy, scope and model metadata. |
| `ModelGateway.invoke` route filtering and priority fallback | `model_gateway.py` | Keep. Change operation mapping and make post-submit async failures non-fallback. |
| `required_route_id` and `required_model_revision` | `model_gateway.py` | Keep and use for status calls; add a persisted immutable route snapshot for jobs. |
| In-memory and Redis budget ledgers | `model_gateway.py` | Keep. Add operation/test scopes and route cap accounting. |
| In-memory and Redis circuit stores | `model_gateway.py` | Keep. Add read-only state/last-transition methods for admin health. |
| `ProviderErrorInfo` and provider status classification | `apps/api/app/services/provider_resilience.py` | Keep; add `sent`/`commit_reached` to distinguish safe fallback from submit uncertainty. |
| `PlanFeatureQuotaGuard` and `FEATURE_QUOTAS` | `apps/api/app/services/billing.py` | Keep; remove vendor-keyed quota categories and count by Motionly feature/category. |
| `OperationsGatewayTelemetry` and `provider_slo_events` fields | `model_gateway.py`, migration `202607140010_provider_neutral_model_gateway.sql` | Keep; use route id/model revision/capability as stable labels and never store prompts/media/secrets. |
| `OpenAICompatibleAdapter` and `AzureOpenAIAdapter` | `apps/api/app/services/model_adapters.py` | Keep behind registry adapter types; the former covers many compatible endpoints. |
| Local adapters for visual manifest, audio intelligence, CLIP, matte and stems | `apps/api/app/services/model_domain_adapters.py` | Keep behind a neutral local adapter; remove vendor/model defaults from core settings. |
| `route_catalog_fingerprint` and embedding cache-version logic | `model_gateway.py`, `model_domain_adapters.py` | Keep; route/model revision changes must invalidate caches. |
| `ai_feature_report`, `gpu_worker_switches` and `degraded_capabilities` | `apps/api/app/services/ai_features.py`, `remote_visual.py` | Keep the behavior, replace implementation with registry-derived capability state. |
| `_FILE` secret loading and media/ML subprocess environment allowlists | `core/config.py`, `apps/api/app/services/subprocess_env.py`, `apps/worker/src/subprocess-env.mjs` | Generalize secret loading; preserve allowlists so generic secrets never reach ffmpeg/Chromium/Demucs unnecessarily. |
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

Old `ModelOperation` values remain aliases during Release N. New telemetry and configuration use the ids above.

### 2.2 Adapter registry

The registry is declarative and has no one-environment-variable-per-vendor design. An adapter has:

```yaml
id: operator-endpoint-1
 type: openai_compatible
 base_url: https://example.invalid/v1
 auth:
   type: bearer
   secret_ref: db:operator-endpoint-1
 network: public
 timeout_s: 60
 health:
   method: GET
   path: /models
 extra_headers: {}
 extra_body: {}
 data_processor:
   display_name: Operator-configured endpoint
   purpose: Inference request processing
   data_categories: [audio, images, text]
   policy_url: https://example.invalid/policy
```

Supported adapter types:

1. `openai_compatible`: chat, vision chat, audio input, transcription, embeddings and image generation where the endpoint supports the relevant Motionly contract. It covers compatible hosted and self-hosted endpoints, including OpenRouter, Groq, Together, vLLM, Ollama, LM Studio and llama.cpp without core code branches.
2. `azure_openai`: deployment-scoped URLs and Azure auth; implementation-specific behavior is isolated under the adapter.
3. `aws_bedrock`: IAM credential chain and SigV4; model-family request mapping is adapter/config data.
4. `huggingface_endpoint`: TGI/TEI/pipeline modes with typed response mappings.
5. `generic_http`: arbitrary HTTP/JSON/multipart request and response mapping templates.
6. `async_job`: declarative submit, poll, result/artifact and cleanup steps; can use generic HTTP operations.
7. `local`: an in-process or local-worker runner for CPU/GPU models, selected by route metadata.
8. `fake`: deterministic CI/dev adapter, rejected in production.
9. `agent_pool`: reserved design type for a later outbound-only GPU agent; not built in this plan.

Auth types:

- `none` for private endpoints only
- `bearer`
- `custom_header` with a configured header name
- Azure `api-key`
- AWS SigV4 with IAM credential chain
- OAuth2 client credentials with token URL, client secret reference and scope

Generic templates use a restricted expression language, not executable Jinja. Allowed operations are field selection, literals, bounded list mapping, base64 encoding, UUID/random opaque id generation, numeric conversion, seconds-to-milliseconds, clamping, sorting and named Motionly normalizers. A template cannot alter the adapter host, access environment variables, execute code or make an unapproved second network request.

### 2.3 Route table

A route record contains:

- `id`
- `capability`
- `adapter_id`
- `model_id` and an allowlist model reference
- immutable `model_revision`/SHA
- `priority` and explicit fallback order
- `strategy`: `native`, `chat_prompt`, `generic_mapping`, `async_job`, or `local`
- `pricing`: unit, input/output price, per-request/per-unit price, minimum billable units, or `explicitly_free`
- `budget_feature` and optional monthly budget cap
- `timeout_s`
- `requires_gpu` / `compute_requirement: cpu|gpu|remote|any`
- request limits: batch size, bytes, duration and output units
- `enabled`
- `scope: global|workspace:<id>` (workspace scope disabled initially)
- `data_processor` reference for public privacy metadata
- `source: file|database`

The route's provider label is metadata for operators and telemetry only. Capability availability, quota, fallback and UI behavior never branch on that label.

### 2.4 Config file, database and admin management

Both configuration sources are supported:

- **File/seed:** `AI_REGISTRY_FILE` points at a JSON or YAML registry used for bootstrap, reproducible deployments and CI. File entries are immutable/read-only in the UI and can be replaced by a deployment rollout.
- **Database/admin:** `ai_adapters`, `ai_models`, `ai_routes` and `ai_secrets` are edited through admin-only API/UI. A registry version counter triggers atomic runtime reload. File and database ids must not collide.

Recommended scope is **global operator keys now**. The database carries scope fields for future true BYOK, but workspace-scoped routes are rejected while the feature flag is false. When workspace BYOK is later enabled, it should require public-host restrictions, explicit workspace billing/privacy policy and route isolation.

Secret rules:

- Secret refs are names such as `db:route-secret`, `file:operator-bundle`, or `bundle:operator-secrets`; values never appear in route responses.
- DB secrets use AES-256-GCM with a deployment KEK from `AI_SECRETS_KEK_FILE`; AAD binds secret name, scope and key version.
- Key rotation supports current and previous KEK during a bounded rotation window; a re-encryption command rewrites ciphertexts.
- Secret metadata exposes name, scope, fingerprint and timestamps only.
- Resolved values are registered with the generic redactor before use.
- Secrets are never passed into ffmpeg, Chromium or unrelated media subprocesses. Existing allowlists in `apps/api/app/services/subprocess_env.py` and `apps/worker/src/subprocess-env.mjs` remain mandatory.

### 2.5 Async operation model

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

Startup validation is capability-based:

- Fail startup if no valid enabled route exists for `planning.chat`.
- Fail startup if no valid enabled route exists for `transcription.speech_to_text`.
- For optional capabilities, omit invalid/incompatible routes from active execution, log a route-id warning, and expose `unavailable` or `degraded` in `/capabilities`.
- Validate auth for every enabled remote route. `auth:none` is valid only for a private route.
- Reject floating/unpinned revisions.
- Require pricing on paid routes and an explicit free flag on zero-priced routes.
- Reject fake routes in production.
- Exclude `requires_gpu` routes when `GPU_WORKERS_ENABLED=false`.
- Warn on non-commercial models; optionally fail when `AI_BLOCK_NONCOMMERCIAL=true`.
- Require data-processor metadata for enabled remote routes.
- Permit a free planning route only when its adapter is private/self-hosted; this does not block a future private CPU/GPU planner.

`GET /capabilities` returns capability id, availability, reason, async status, compute requirement and route count. It never returns base URLs, model secrets or sensitive provider metadata. A separate admin route returns detailed route/circuit/latency information.

### 2.7 Operator tooling

Admin-only endpoints/UI provide:

- Add/edit/disable adapters, models and routes.
- Write-only secret creation/rotation; no secret read endpoint.
- `POST /admin/inference/routes/{route_id}/test` with a tiny typed sample, schema validation, latency, usage and cost. Async test submits require explicit `allow_paid=true` and use a separate operator budget scope.
- `GET /admin/inference/routes/health` with circuit state, last success/failure, p50/p95 latency, error rate, fallback count and budget usage.
- Route validation before save and a dry-run registry import.
- License/commercial-use warnings.

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

Every model entry records:

`id`, external model id, pinned revision/SHA, license SPDX/proprietary marker, commercial-use flag, source type, source URL, capability list, minimum VRAM, weights SHA-256 and verification timestamp.

Non-commercial licenses produce a startup/admin warning and UI badge. Implementers must verify each existing local model's current license from its authoritative source during the model-allowlist task; the implementation must not infer licensing from model names.

---

## 3. No-GPU reality matrix

The baseline uses remote routes where the current packaged CPU path is insufficient and local CPU routes where they already exist.

| Capability | Remote-only/CPU execution today | Cheapest viable route type | Graceful no-route behavior | GPU or remote required? |
|---|---|---|---|---|
| `planning.chat` | Remote chat API; a private CPU llama.cpp-compatible endpoint can be added later | `openai_compatible` | Startup fails because required | Yes in the current packaged baseline; private CPU endpoint can remove the remote dependency later |
| `transcription.speech_to_text` | Remote multipart STT route; current non-GPU topology already expects remote transcription | `openai_compatible` | Startup fails because required | Yes in current baseline; CPU local inference is a future adapter route |
| `vision.caption` | Remote vision/chat route | `openai_compatible` or `generic_http` | Visual manifest marks captions unavailable/degraded; editing continues with transcript/CPU signals | Remote or a future CPU/GPU local model |
| `vision.object_detection` | Remote mapped HTTP route | `generic_http` | Empty detections and degraded capability; no hard failure of editing | Remote today; a CPU route could be added later |
| `embeddings.text` | Remote embeddings or local CPU CLIP if the worker image is configured for it | Local CPU route is free; otherwise `openai_compatible` | B-roll ranking falls back to lexical matching | No if CPU CLIP is installed; otherwise remote |
| `embeddings.image` | Remote image embeddings or local CPU CLIP | Same embedding-space route as text | Keyframe/stock vector enrichment is skipped | No if CPU CLIP is installed; otherwise remote |
| `audio.diarization` | Remote async job; current local pyannote path is not part of the CPU-only image contract | `async_job` | Transcript uses the existing heuristic/single-speaker fallback and marks degraded | Remote or a future CPU local model |
| `audio.emotion` | Remote audio-chat route; local wav2vec2 remains a GPU/local route unless separately packaged for CPU | `openai_compatible` with audio input | Empty emotion windows; transcript remains usable | Remote or local model runtime |
| `audio.stem_separation` | Demucs can run on CPU, slower; remote async route is also supported | Local CPU `local` route is cheapest; generic async remote is fallback | Stem control disabled; core edit remains usable | No, CPU works |
| `image.generation` | Remote image API | `openai_compatible` or `generic_http` | Feature is unavailable/hidden; no current required caller fails | Remote today |
| `video.generation.submit/status` | Remote async generation API | `async_job` | Generated B-roll control disabled with clear unavailable state | Remote today |
| `matte.segmentation` | MediaPipe CPU route via the existing CPU vision topology | Local CPU `local` route | Matte-dependent background effects are disabled | No, CPU works |
| `audio.sound_classification` | Existing YAMNet TFLite CPU path | Local CPU `local` route | Labels are empty and audio intelligence is degraded | No, CPU works |

A no-GPU production acceptance profile must configure remote planning and transcription, and must either configure remote or CPU routes for any feature the operator wants enabled. No GPU Compose service may be required by startup validation when `GPU_WORKERS_ENABLED=false`.

---

## 4. Dependency-ordered implementation plan

The task numbers are globally ordered. Each item includes actual files to modify, proposed new files where necessary, migrations, tests, rollout/back-compat, risks and an acceptance criterion.

### Phase 0 — offline test harness and guardrails

#### 1. Establish a shared fake adapter and no-network CI contract

**Dependencies:** none.

**Files:**

- Reuse and consolidate `apps/api/tests/test_model_gateway.py` and `apps/api/tests/test_model_gateway_feature_budgets.py` fake adapters.
- **NEW (proposed):** `apps/api/app/services/inference_fake.py` or a neutral fake implementation under the existing API service tree.
- **NEW (proposed):** `apps/api/tests/support/fake_provider_server.py`.
- `apps/api/tests/conftest.py`.

**Migration:** none.

**Implementation:** fake every typed capability, including success, schema-invalid response, 400, 401/403, 402, 429, 5xx, timeout-before-send and timeout-after-send. Make normal tests fail if they open a real network socket.

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

**Implementation:** scan runtime/config/UI/infra/test source with word-boundary patterns for the audited vendor names. Allow documented examples and adapter implementation tests only. During the transition, allow the compatibility translator and transitional template fixtures explicitly and with an expiry. Historical migrations are reported separately because applied migration files cannot be rewritten safely.

**Tests:** scanner unit tests for word boundaries, allowlist path matching, generated files and a deliberate forbidden string.

**Rollout/back-compat:** report-only baseline first; enforce after Task 26.

**Risks:** false positives in model ids and historical data. Treat protocol terms separately and show every allowlisted match in the report.

**Acceptance criteria:** the report names every current finding; a new audited vendor string in core API, web, infra or a new migration fails the enforce mode.

### Phase 1 — typed taxonomy, registry and secrets

#### 3. Introduce the fine-grained Motionly capability taxonomy

**Dependencies:** 1.

**Files:**

- `apps/api/app/services/model_gateway.py`.
- `apps/api/app/services/model_provider_bridges.py`.
- **NEW (proposed):** `apps/api/app/services/inference_capabilities.py`.
- **NEW (proposed):** `apps/api/app/services/inference_schemas.py`.

**Migration:** none.

**Implementation:** add all capability ids from Section 2.1; add typed requests/responses for captions, emotion, status and image generation; provide aliases from old `ModelOperation` values; ensure `capability_for_request` and `operation_for_request` are exhaustive.

**Tests:** every capability has request validation and response validation; old serialized operation names remain readable; metadata emits new ids; embedding vectors enforce aligned dimensions.

**Rollout/back-compat:** re-export old classes/operation aliases for one release. Do not change persisted historical operation strings in place.

**Risks:** broad type churn. Keep compatibility imports until all callers move.

**Acceptance criteria:** a table-driven test covers every capability and no request type falls through to a generic untyped dictionary.

#### 4. Add a declarative registry file schema and validator

**Dependencies:** 3.

**Files:**

- `apps/api/app/core/config.py`.
- **NEW (proposed):** `apps/api/app/services/inference_registry.py`.
- **NEW (proposed):** `docs/ai-registry-example.json` or `docs/ai-registry-example.yaml`.

**Migration:** none.

**Implementation:** define adapter, model allowlist, route, auth, price, limits and data-processor records. Load JSON first; add YAML only if a pinned YAML dependency is approved. Validate ids, references, supported capability/adapter combinations, HTTPS/private network policy, pinned revisions, price/free declaration, embedding space and GPU requirement.

**Tests:** valid/invalid registry fixtures, duplicate ids, unknown references, unpinned revisions, URL credentials/query strings, missing auth, invalid pricing and non-commercial warnings.

**Rollout/back-compat:** not active by default until Task 6; legacy environment translation remains the fallback source.

**Risks:** config schema becomes an accidental public API. Version the registry format and reject unknown major versions.

**Acceptance criteria:** a sample registry loads deterministically, and invalid optional routes are returned as route-specific diagnostics without hiding errors in required routes.

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

**Implementation:** support `none`, bearer, custom header, Azure api-key, OAuth2 client credentials and a placeholder SigV4 strategy. Resolve file/bundle refs now; DB refs are completed in Task 20. Register resolved secret values with redaction. Reject `env:` secret refs in production.

**Tests:** missing ref, file ref, auth header construction, OAuth token caching/refresh, exact secret redaction in logs/reprs/exceptions, no secret in route responses.

**Rollout/back-compat:** old `_FILE` values are translated for one release; generic bundle support is additive.

**Risks:** leaked secrets through exception context or request logging. Add tests around every auth failure and ensure middleware does not log request bodies.

**Acceptance criteria:** an injected test secret is absent from all captured logs, error messages, serialized route metadata and fake-provider request logs except the in-memory request sent to the intended fake server.

#### 6. Build one registry-driven runtime and legacy environment translator

**Dependencies:** 3, 4, 5.

**Files:**

- `apps/api/app/services/model_gateway_factory.py` becomes a thin compatibility wrapper.
- `apps/api/app/main.py`.
- `apps/api/app/core/production.py`.
- `apps/api/app/workers/visual_job.py` and `apps/api/app/workers/matte_job.py`.
- **NEW (proposed):** `apps/api/app/services/inference_runtime.py`.
- **NEW (proposed):** `apps/api/app/services/legacy_inference_config.py`.
- **NEW (proposed):** `apps/api/app/ops/convert_legacy_inference_config.py`.

**Migration:** none.

**Implementation:** registry is authoritative when configured; otherwise translate current settings to equivalent routes while preserving route ids/revisions and emitting deprecation warnings. The converter writes a generic registry file and secret-reference manifest without printing secret values. API and workers use the same builder and route fingerprint.

**Tests:** parity tests compare legacy and registry route catalogs, pricing, operation bindings, cache fingerprints and required-route behavior. Worker/API construction tests use fake routes.

**Rollout/back-compat:** exactly one compatibility release. Existing environment files continue to work. The translator must be removed in Task 26.

**Risks:** differing API/worker catalogs could cause queued jobs to fail. Include registry version and catalog fingerprint in worker heartbeats and fail closed on mismatch for required operations.

**Acceptance criteria:** a legacy configuration and its converted registry produce equivalent planning/STT behavior and route metadata against the fake provider.

### Phase 2 — capability validation and exposure

#### 7. Replace vendor startup checks with capability validation

**Dependencies:** 6.

**Files:**

- `apps/api/app/core/production.py`.
- `apps/api/app/core/config.py`.
- `apps/api/app/main.py`.
- **NEW (proposed):** `apps/api/app/services/inference_validation.py`.
- Existing `apps/api/tests/test_service_hardening.py`, `test_gpu_free_deployment.py`, `test_health.py`.

**Migration:** none.

**Implementation:** remove checks for individual vendor keys and Runway generation. Require planning/STT route availability only. Validate auth, pinned revision, pricing/free, fake adapter, data-processor metadata and GPU suitability generically. Preserve unrelated production checks for Supabase, Redis, Temporal, billing and worker roles.

**Tests:** missing planning fails; missing STT fails; optional missing video/vision/audio boots; bad auth/price/revision is rejected; no-GPU configuration excludes GPU routes; private free route is valid.

**Rollout/back-compat:** legacy translator supplies required routes during Release N. Optional vendor-specific features may become unavailable until configured in the registry; this is expected and visible.

**Risks:** optional misconfiguration could be silent. Every omitted route must create a structured warning and capability status.

**Acceptance criteria:** a production settings object with only generic registry routes for planning and STT passes startup, while a registry missing either required capability fails with a capability-specific error.

#### 8. Add `/capabilities` and registry-driven provider health

**Dependencies:** 7.

**Files:**

- `apps/api/app/api/health.py`.
- `apps/api/app/main.py`.
- Existing `apps/api/app/services/ai_features.py` is replaced or reduced to a compatibility wrapper.
- **NEW (proposed):** `apps/api/app/api/capabilities.py`.
- **NEW (proposed):** `apps/api/app/services/inference_capability_report.py`.
- `packages/shared/openapi.json`, `packages/shared/src/generated/api.ts` regenerated by the existing OpenAPI process.

**Migration:** none.

**Implementation:** `GET /capabilities` exposes status, reason, async flag, compute requirement and route count. Health uses adapter-declared probe requests and circuit state; it does not have a vendor list. Keep public liveness/readiness inexpensive and authenticated diagnostics protected as today.

**Tests:** capability matrix with empty, valid, failed and circuit-open routes; no secret/base URL leakage; fake health responses; OpenAPI contract regeneration.

**Rollout/back-compat:** health response adds fields without removing existing health fields. Existing `gpu_stage` stays until the UI no longer relies on it.

**Risks:** endpoint information disclosure. Return only capability-level data publicly; detailed route status is admin-only.

**Acceptance criteria:** no configured optional route produces `unavailable` with a machine-readable reason, and a route with an open circuit produces `degraded` rather than making the whole API unhealthy.

#### 9. Gate the UI on capabilities and make privacy metadata config-driven

**Dependencies:** 8.

**Files:**

- `apps/web/components/editor/ScalePanel.tsx`.
- `apps/web/components/editor/ai-step.tsx`.
- `apps/web/components/editor/tabs/audio-tab.tsx`.
- `apps/web/app/privacy/page.tsx`.
- Existing source-contract and browser tests under `apps/web/tests/`.
- **NEW (proposed):** `apps/web/lib/capabilities.ts` and `apps/web/hooks/useCapabilities.ts`.

**Migration:** none.

**Implementation:** fetch capability status, disable unavailable controls with visible text such as `Unavailable — not configured by your operator`, preserve keyboard/focus behavior and use `aria-disabled`. Replace hard-coded processor privacy copy with sanitized adapter `data_processor` metadata from a public metadata endpoint.

**Tests:** missing route, degraded route, available route, keyboard accessibility, no secret/model URL in client payload, privacy page exactly reflects configured processors.

**Rollout/back-compat:** UI keeps existing degraded visual/transcript behavior. Runway copy changes to Generated B-roll when the B-roll migration lands.

**Risks:** stale client capability cache. Include registry version and short polling/refresh after admin changes.

**Acceptance criteria:** disabling a route changes the corresponding control to a clear unavailable state without breaking unrelated editing, and no vendor-specific provider name is required in the UI.

### Phase 3 — move consumers to capabilities

#### 10. Migrate planning and transcription to gateway-only routes and feature quotas

**Dependencies:** 6, 7, 8.

**Files:**

- `apps/api/app/services/model_provider_bridges.py`.
- `apps/api/app/services/edit_planner.py`.
- `apps/api/app/services/transcription_provider.py`.
- `apps/api/app/main.py`.
- `apps/api/app/services/billing.py`.
- `apps/api/app/core/config.py`.
- Related tests: `test_plans.py`, `test_phase3_ai_ml_reliability.py`, `test_feature_quotas.py`, `test_model_gateway.py`, `test_route_callers.py`.

**Migration:** add an index on `cost_attribution_events(workspace_id, category, created_at)` if schema inspection confirms it is absent. Preserve existing provider/category rows as historical usage.

**Implementation:** remove direct HTTP planner/STT fallback from production and use typed gateway bridges. Add `planning` and `transcription` feature quota categories. The quota guard counts by category, not `route.provider`, so changing route/provider cannot reset a workspace quota.

**Tests:** end-to-end fake upload/transcription/plan; switch primary route and verify accumulated quota remains; retries and validation behavior remain unchanged.

**Rollout/back-compat:** old provider quota settings map to feature settings for one release. Existing usage rows are included through a category mapping/backfill view or compatibility query.

**Risks:** workspaces may reach a correctly consolidated quota earlier than before. Announce this behavior and include migration diagnostics.

**Acceptance criteria:** planning and STT succeed against two differently labeled routes without consumer code changes, and quota usage is continuous across the route switch.

#### 11. Convert captions and emotion to generic typed strategies

**Dependencies:** 3, 8, 10.

**Files:**

- `apps/api/app/services/remote_visual.py`.
- `apps/api/app/services/remote_audio_emotion.py`.
- `apps/api/app/services/model_domain_adapters.py`.
- **NEW (proposed):** `apps/api/app/services/inference_strategies.py`.
- Tests `test_remote_visual_captions.py`, `test_remote_audio_emotion.py`, `test_remote_visual_e2e.py`.

**Migration:** none.

**Implementation:** Motionly owns prompt, response schema and normalization. Any compatible vision/chat route can serve the strategy. Route config carries optional no-retention/request-body flags without naming a vendor.

**Tests:** fake chat/vision provider, malformed structured response, partial caption failure and degraded manifest behavior.

**Rollout/back-compat:** old route ids are emitted by the translator for one release; existing cache identity includes model revision and route catalog.

**Risks:** prompt or JSON parsing regression. Port existing prompt fixtures exactly before refactoring.

**Acceptance criteria:** captions and emotion work with any configured compatible route and become degraded, not fatal, when no optional route exists.

#### 12. Convert text/image embeddings to configured routes

**Dependencies:** 3, 6, 8, 10.

**Files:**

- `apps/api/app/services/model_adapters.py`.
- `apps/api/app/services/model_domain_adapters.py`.
- `apps/api/app/services/remote_visual.py`.
- `apps/api/app/services/stock_video.py`.
- `apps/api/app/services/layer_jobs.py`.
- `apps/api/app/api/broll.py`.
- Existing `test_jina_embeddings.py`, B-roll and visual tests.

**Migration:** none; preserve cache versioning and force re-embedding when `embedding_space` or model revision changes.

**Implementation:** support configured text/image input mappings in `openai_compatible`/`generic_http`; delete dedicated Jina route construction after conformance. Require matching embedding space for compared vectors. Keep lexical fallback.

**Tests:** dimensions, input ordering, image MIME validation, vector normalization and cache invalidation; route/provider switch does not corrupt B-roll ranking.

**Rollout/back-compat:** translator emits the old embedding route identity and model revision. Existing cache keys remain valid only when the declared embedding space and revision match.

**Risks:** mixed vector spaces silently produce bad ranking. Validation must reject mismatched spaces before activation.

**Acceptance criteria:** both text and image embeddings can be added through route config, and missing embeddings produce lexical ranking plus an unavailable capability state.

#### 13. Put existing object, diarization and stems behind a transitional registry adapter

**Dependencies:** 6, 8, 11.

**Files:**

- `apps/api/app/services/google_vision.py`.
- `apps/api/app/services/pyannoteai_diarization.py`.
- `apps/api/app/services/lalal_stems.py`.
- `apps/api/app/services/model_domain_adapters.py`.
- `apps/api/app/services/remote_visual.py`.
- `apps/api/app/main.py`.
- **NEW (proposed):** `apps/api/app/services/inference_transitional_adapters.py`.

**Migration:** none.

**Implementation:** stop registering routes through vendor-named functions. For one compatibility release, a transitional adapter loads the same behavior from route metadata, including batch limits, minimum billable duration and route revisions. This step preserves behavior while the generic templates are built; it is explicitly temporary and must be removed in Task 17.

**Tests:** existing `test_google_vision_objects.py`, `test_pyannoteai_diarization.py`, `test_lalal_stems.py` remain green; route removal yields optional unavailable state.

**Rollout/back-compat:** legacy env translator generates transitional route records with stable ids/revisions.

**Risks:** temporary code becomes permanent. Add an expiry assertion to the neutrality scanner and a deletion checklist.

**Acceptance criteria:** each of the three capabilities runs through the gateway and registry route selection, with no direct registration call from application startup.

#### 14. Move local models and license metadata into routes

**Dependencies:** 4, 7, 12, 13.

**Files:**

- `apps/api/app/services/ai_features.py`.
- `apps/api/app/workers/prefetch_models.py`.
- `apps/api/app/services/stock_video.py` and `apps/api/app/services/layer_jobs.py` where local model defaults currently live.
- `apps/api/app/core/config.py` local model fields.
- **NEW (proposed):** `infra/ai-registry/cpu-local.json` and `infra/ai-registry/gpu-local.json`.

**Migration:** none.

**Implementation:** local CPU/GPU model entries declare capability, revision, license, commercial flag and minimum VRAM. `requires_gpu` is the only placement signal. Model prefetch reads registry entries rather than hard-coded defaults.

**Tests:** CPU registry enables matte/stems/sound; GPU routes are excluded when disabled; license warning and optional block; model cache uses revision/SHA.

**Rollout/back-compat:** legacy variables translate to local model entries for one release.

**Risks:** model licenses and weight revisions require verification. No license is accepted without an authoritative source URL and verification timestamp.

**Acceptance criteria:** a no-GPU local registry can start CPU-capable features without loading GPU-only packages, and every active model has pinned revision and license metadata.

### Phase 4 — generic HTTP, async jobs, template conversion and generated B-roll

#### 15. Implement the restricted `generic_http` mapping adapter

**Dependencies:** 4, 5, 11, 12, 13.

**Files:**

- `apps/api/app/services/model_adapters.py` or a neutral adapter module under the existing service tree.
- `apps/api/app/services/media_url_policy.py` for SSRF policy reuse.
- **NEW (proposed):** `apps/api/app/services/inference_templates.py`.
- **NEW (proposed):** `apps/api/app/services/inference_normalization.py`.

**Migration:** none.

**Implementation:** support JSON/multipart request mappings, bounded `$map`, response extraction, input-aligned result arrays, usage counting, box conversions (`xywh_norm`, `xyxy_px`, `vertices_norm`), label aliases, status/error maps and Motionly normalizers. Templates cannot change host, execute code or read secrets.

**Tests:** config-only caption, object, STT, embeddings and audio request against fake server; path/host injection; oversized payloads; partial item errors; malformed response classification; normalized box and label tests.

**Rollout/back-compat:** additive; transitional adapters remain until Task 17 parity passes.

**Risks:** a template language can become arbitrary code. Keep the grammar small and reject unknown functions/fields before activation.

**Acceptance criteria:** a new synchronous object-detection endpoint can be integrated by adding registry config and fixtures only; no Python adapter class changes are required.

#### 16. Add persisted async operations and route-affine `async_job`

**Dependencies:** 5, 7, 15.

**Migration:** **NEW (proposed):** `supabase/migrations/<timestamp>_inference_operations.sql` and matching `supabase/tests/inference_operations_test.sql`.

Schema requirements:

- `inference_operations.id`
- tenant/project references and RLS
- `capability`, `idempotency_key` with workspace uniqueness
- `provider_route_id`, `provider_model`, `provider_model_revision`
- `route_snapshot` JSONB containing adapter id/type/base URL/model/revision/mapping version and secret ref name, never a secret value
- `provider_operation_id`
- state check: `reserved`, `submitting`, `submitted`, `submit_unknown`, `running`, `succeeded`, `failed`, `cancelled`, `needs_review`
- status/error detail, estimated/actual cost, timestamps
- unique route/provider-operation identity where non-null
- worker grants following the least-privilege patterns in existing worker migrations

**Files:**

- `apps/api/app/services/model_gateway.py`.
- `apps/api/app/services/provider_resilience.py`.
- **NEW (proposed):** `apps/api/app/services/inference_operations.py`.
- **NEW (proposed):** `apps/api/app/services/async_job_adapter.py`.
- Existing queue bridge files only if delayed polling support is missing.

**Implementation:** multi-step submit/poll/result/cleanup templates, commit boundary, pre-send vs after-send errors, exponential poll backoff, maximum wait, artifact downloads with host/size policy, and persisted resume behavior. Blocking callers can wait through a bounded facade; video jobs re-enqueue delayed polls.

**Tests:** double submit idempotency; timeout-after-send never uses fallback; poll uses stored route snapshot after config change; cleanup success/failure; private-IP artifact rejection; size limits; submit-unknown reconciliation.

**Rollout/back-compat:** additive table. Existing payload jobs are readable and are backfilled in Task 18. No old job is resubmitted during migration.

**Risks:** duplicate charges, stuck operations, delayed queue pressure and route secret rotation. Use state-machine constraints, reconciliation metrics and operator `needs_review` state.

**Acceptance criteria:** a fake async job produces a complete persisted trace, and every retry after the commit boundary polls the original operation without a second submit.

#### 17. Convert object detection, diarization and stems from transitional code to templates

**Dependencies:** 13, 15, 16.

**Files:**

- **NEW (proposed):** `docs/ai-registry/examples/object-detection.json`, `diarization.json`, `stem-separation.json` and scenario fixtures.
- **NEW (proposed):** `apps/api/tests/test_inference_template_conformance.py`.
- `apps/api/app/services/remote_visual.py` for generic enrichment.
- `apps/api/app/services/scale.py` for generic stem operation handling.
- Existing provider implementation files only for the deletion step.

**Migration:** none.

**Implementation requirements taken from the audited code:**

- Object detection: per-image request mapping, max batch/max bytes, aligned response array, partial image errors, billable successful image count, normalized-vertex boxes, alias/clamp/sort/top-N normalization.
- Diarization: random opaque media id, signed upload with no API auth on the upload host, submit commit boundary, pending/running/terminal status map, tolerated transient polls, backoff/deadline, seconds-to-milliseconds conversion, minimum billable units.
- Stems: audio-only FLAC transform using the existing safe media subprocess environment, neutral upload filename, idempotent split request, POST status poll, required vocals/no-vocals artifacts, HTTPS/provider-host allowlist, byte cap, cleanup in finally and temp-folder cleanup.

**Tests:** port current vendor adapter test scenarios to generic conformance fixtures. Assert outgoing request mapping, typed output, usage, classification, cleanup, SSRF defense and no secret leakage. Keep one manually authorized live contract run before deleting old adapters if desired.

**Rollout/back-compat:** route ids, revisions, cache identities and output schemas remain stable. The legacy translator emits these examples for one release.

**Risks:** mapping language may be insufficient for edge behavior. Extend generic primitives, not with a new provider-specific branch.

**Acceptance criteria:** all conformance scenarios for the three existing integrations pass through `generic_http`/`async_job`; transitional provider files and their direct imports can be deleted without changing typed outputs.

#### 18. Migrate generated B-roll to neutral async video operations

**Dependencies:** 16, 17.

**Migration:** **NEW (proposed):** `supabase/migrations/<timestamp>_neutral_generated_broll.sql`.

Migration steps:

1. Add `generated` to `public.stock_provider` in a forward-compatible migration. Do not edit the historical migration that added the old enum value.
2. Backfill `stock_assets.provider` and `broll_slots.provider` from the old value to `generated`.
3. Stop writing the old value in code; add a constraint or data validation preventing new old-value rows once the rollout is complete.
4. Create/backfill `inference_operations` for `phase5_resources` rows with `kind='generated_broll'`.
5. Copy old JSONB `runway_task_id` to `provider_operation_id`, old route id to `provider_route_id`, and old model revision to `provider_model_revision`; preserve the original payload during the compatibility window.
6. Add a neutral operation id to the payload for readers that still use phase5 resources.
7. Keep old payload keys dual-readable for one release and make new writes neutral.

**Files:**

- `apps/api/app/services/scale.py` and `apps/api/app/services/job_engine.py`.
- `apps/api/app/api/scale.py`.
- `apps/api/app/schemas/broll.py`, `apps/api/app/repositories/broll.py`.
- `packages/shared/src/index.ts`, `packages/shared/openapi.json`, generated API types.
- `apps/web/components/editor/ScalePanel.tsx`.
- `apps/api/app/services/billing.py` feature quota mapping.

**Tests:** migration contract tests, old-data rendering, new operation fields, idempotent resume polling, generated asset selection, API/UI copy and contract regeneration.

**Rollout/back-compat:** deploy readers and writers that understand `generated` before backfill; do not allow an old writer to run after the backfill step. Keep old enum value physically present because applied Postgres migrations cannot safely remove it.

**Risks:** rolling deploy race, existing jobs with only JSONB task id, duplicate video charges and API contract drift.

**Acceptance criteria:** old generated B-roll still renders, all new jobs populate `provider_operation_id`, `provider_route_id` and `provider_model_revision`, and a retry never submits an already-submitted operation again.

#### 19. Remove Runway code, secrets, configuration and UI coupling

**Dependencies:** 17, 18.

**Files to clean after dual-read/backfill:**

- Code: `apps/api/app/services/scale.py`, `model_domain_adapters.py`, `job_engine.py`, `main.py`, `core/config.py`, `core/production.py`, `api/health.py`, `services/billing.py`, `core/logging.py`, `schemas/broll.py`, `repositories/broll.py`.
- Config/infra: `.env.example`, `apps/api/.env.example`, `apps/worker/.env.example`, `prod.env.example`, `infra/production-compose.env.example`, `infra/compose-host/host.env.example`, `infra/docker-compose.gpu.yml`, `infra/docker-compose.remote-ai.yml`.
- CI/scripts: `.github/workflows/live-provider-contracts.yml`, `scripts/deploy/apply_release.sh`, `scripts/release/write_compose_test_env.py`, `setup-local-env.sh`, `setup-local-env.ps1`, `apps/api/scripts/audit_local_capabilities.py`.
- UI/contracts/docs/tests: `apps/web/components/editor/ScalePanel.tsx`, `packages/shared/openapi.json`, `packages/shared/src/generated/api.ts`, `packages/shared/src/index.ts`, affected API tests and historical vendor fixtures.

**Migration:** no new schema migration beyond Task 18; old enum/data history is handled by Task 18.

**Implementation:** delete Runway adapter/provider/fixture classes, keys, Compose secret, health probe, production requirement, quota names, direct endpoint, error strings and UI copy. Use neutral generated-video wording and capability id.

**Tests:** repository scan, all scale/gateway/health/production/Compose/UI tests, old-data migration test and no secret-file reference test.

**Rollout/back-compat:** operator removes the old secret file and external secret-store entry only after all services run code that no longer reads it. The Release N translator may still recognize old variables until Task 26, but no production runtime path requires them.

**Risks:** missing a string in generated OpenAPI or release scripts. Task 27 is the hard gate.

**Acceptance criteria:** no new runtime/config/UI/infra/test file contains the old vendor name; old completed rows remain readable through neutral accessors; generated B-roll uses only neutral capability/route terminology.

### Phase 5 — database registry, admin API and UI

#### 20. Add DB registry and AES-256-GCM encrypted global secrets

**Dependencies:** 4, 5, 7, 16, 19.

**Migration:** **NEW (proposed):** `supabase/migrations/<timestamp>_inference_registry.sql` and `supabase/tests/inference_registry_test.sql`.

Tables:

- `ai_adapters`: id, type, base URL, auth metadata, network policy, health/mapping config, data-processor metadata, enabled, timestamps.
- `ai_models`: model id, external id, revision/SHA, license, commercial-use flag, source/source URL, capabilities, minimum VRAM, weights SHA, verification metadata.
- `ai_routes`: route id, capability, adapter/model references, revision, priority/fallback, strategy, pricing, budget, limits, compute requirement, scope, enabled, timestamps.
- `ai_secrets`: scope/name, ciphertext, nonce, key version, fingerprint, creator and rotation/use timestamps; no plaintext column.
- `ai_registry_version`: singleton/version counter updated transactionally on registry writes.

Apply RLS, service-role access and restricted worker grants based on existing least-privilege migration patterns. Workspace scope columns exist, but a feature flag defaults false and database/API validation rejects non-global entries while disabled.

**Files:** `apps/api/app/core/config.py`, **NEW (proposed)** `apps/api/app/services/inference_registry_store.py`, `inference_secrets.py`, `inference_runtime.py`.

**Tests:** encryption round-trip, wrong AAD rejection, KEK rotation, no plaintext in API/DB logs, RLS, worker permissions, atomic version bump, reload preserving active calls and global-scope enforcement.

**Rollout/back-compat:** file registry remains a complete fallback. DB registry is additive; migrate no existing secret until operator explicitly imports it.

**Risks:** losing KEK loses secrets. Document secure backup, rotation and break-glass procedure before enabling DB secrets.

**Acceptance criteria:** an admin-created global secret and route become active after registry reload without an API restart, and the plaintext secret is never returned or logged.

#### 21. Add admin inference registry API

**Dependencies:** 20.

**Files:**

- `apps/api/app/api/admin.py` or a new router included from it.
- **NEW (proposed):** `apps/api/app/api/admin_inference.py` and `apps/api/app/schemas/admin_inference.py`.
- `apps/api/app/services/admin_user_directory.py` for existing audit-log conventions.
- `apps/api/tests/test_admin.py`, `test_role_route_inventory.py`.
- Shared OpenAPI output.

**Migration:** none beyond Task 20.

**Implementation:** CRUD/dry-run for adapters, models and routes; write-only secret create/rotate/delete; metadata-only secret list; reject route deletion with nonterminal operations; audit every change; validate registry before commit.

**Tests:** every endpoint, admin authorization, non-admin 403, secret absence in responses/logs, duplicate ids, invalid route, RLS/service boundaries and rate limiting.

**Rollout/back-compat:** admin-only and disabled until DB registry is verified. File entries are shown as managed/read-only.

**Risks:** request-body logging and privilege escalation. Confirm middleware redaction and use `require_admin` for every route.

**Acceptance criteria:** an operator can create a global secret, adapter, model and route via API, see only secret metadata afterwards and activate the route through reload.

#### 22. Add route test action and per-route health/circuit status

**Dependencies:** 8, 20, 21.

**Files:**

- `apps/api/app/services/model_gateway.py` circuit state read API.
- **NEW (proposed):** `apps/api/app/services/inference_route_test.py` and sample fixtures.
- Admin inference router/schemas.
- `provider_slo_events` query/repository code.
- `apps/api/tests/test_model_gateway.py`, `test_metrics_telemetry.py`, new admin inference tests.

**Migration:** add only needed indexes to existing `provider_slo_events` if absent; no provider-specific columns.

**Implementation:** tiny typed samples per capability, per-test cost limit, operator budget scope, optional paid async confirmation, schema-valid result, latency, usage, estimated/actual cost, error code and circuit state. Add `CircuitStore.state()` for Redis/in-memory stores.

**Tests:** success, invalid schema, timeout, auth failure, cost cap, circuit opening/closing, async paid confirmation and route-affine status test.

**Rollout/back-compat:** admin-only; no automatic health probe can submit a paid async job.

**Risks:** accidental spend or sensitive sample data. Use synthetic samples and require explicit `allow_paid` for submit.

**Acceptance criteria:** the operator can test a configured route and receives latency/cost/schema status without seeing a secret; a malformed response is reported as `schema_invalid`.

#### 23. Add admin AI configuration UI

**Dependencies:** 9, 21, 22.

**Files:**

- **NEW (proposed):** `apps/web/app/(app)/admin/ai/page.tsx` and `actions.ts`.
- Existing `apps/web/app/(app)/admin/ops/` patterns and `components/app-shell.tsx`.
- Admin source-contract/browser tests.

**Migration:** none.

**Implementation:** adapter/model/route forms, write-only secret fields, route priority/fallback ordering, license warnings, capability matrix, health/circuit panel, test action and explicit delete confirmations. Use server-side admin auth and no secret values in browser state.

**Tests:** accessibility, admin-only navigation/API, add/test/disable flow, no secret in rendered source or network response, unavailable feature state after disabling a route.

**Rollout/back-compat:** admin-only and additive; existing admin pages unchanged.

**Risks:** exposing internal endpoints or secret fingerprints too broadly. Return only metadata and protect every mutation server-side.

**Acceptance criteria:** an operator can add a key/model/route, test it, reload and see the corresponding editor feature become available without a code or image rebuild.

### Phase 6 — additional protocol adapters

#### 24. Add Hugging Face endpoint adapter

**Dependencies:** 15, 20, 22.

**Files:** **NEW (proposed)** adapter module under the existing API service tree; registry examples; adapter tests.

**Migration:** none.

**Implementation:** support endpoint modes for text-generation/vision/audio, text embeddings and pipeline responses through typed mappings. Bearer secret refs only; no Hugging Face names in core.

**Tests:** fake endpoint for each supported mode, response normalization, timeout/auth/error classification and route test.

**Rollout/back-compat:** additive optional adapter.

**Risks:** endpoint response variation; each capability mapping must be explicit and schema-validated.

**Acceptance criteria:** a registry-only Hugging Face route passes the admin route test and a real feature call through fake contract fixtures.

#### 25. Add AWS Bedrock adapter with IAM/SigV4

**Dependencies:** 15, 20, 22.

**Files:** **NEW (proposed)** adapter module/auth strategy; `apps/api/pyproject.toml` only if an exact optional `botocore` dependency is approved; AWS adapter tests.

**Migration:** none.

**Implementation:** Converse/InvokeModel mappings are adapter/config data; use IAM credential chain and SigV4. No static cloud key is stored in an AI secret for the IAM path.

**Tests:** AWS SigV4 published test vectors, fake Bedrock transport, credential refresh, auth failure and typed schema validation.

**Rollout/back-compat:** optional adapter; no AWS route required for no-GPU baseline.

**Risks:** dependency size and request-shape differences. Keep the adapter optional and protocol tests exhaustive.

**Acceptance criteria:** a route using an IAM role can pass the fake Bedrock contract and the route validator without a static API key.

### Phase 7 — cutover and proof

#### 26. Remove legacy settings and finish docs/Compose/CI cutover

**Dependencies:** 14, 17, 19, 20, 21, 23, 24, 25.

**Files:**

- `apps/api/app/core/config.py`, `model_gateway_factory.py`, `core/production.py`, `services/ai_features.py` and all compatibility consumers.
- `.env.example`, `apps/api/.env.example`, `apps/worker/.env.example`, `prod.env.example`, `infra/production-compose.env.example`, `infra/compose-host/host.env.example`.
- `infra/docker-compose.gpu.yml`, `infra/docker-compose.remote-ai.yml`, `infra/docker-compose.cpu-stage.yml` if registry mounts are added.
- `.github/workflows/live-provider-contracts.yml`.
- `scripts/deploy/apply_release.sh`, `scripts/release/write_compose_test_env.py`, `setup-local-env.sh`, `setup-local-env.ps1`, `apps/api/scripts/audit_local_capabilities.py`.
- `DEPLOYMENT-GUIDE.md`, `docs/generated/motionly_technical_documentation.md`, `docs/security/data-handling.md`, `docs/security/secrets.md`, `docs/ops/compose-vm-deployment.md`, `docs/ops/supply-chain.md`.

**Migration:** no new data migration; this is the post-backfill cutover.

**Implementation:** remove legacy environment fields, translator, vendor `_FILE` list, direct endpoint constants, provider-specific Compose secrets, vendor health probes, direct fallback branches and old quota setting names. Keep a release note/converter reference before deleting the translator.

**Tests:** all API/web/worker type/lint/build tests; Compose config tests; empty-env and GPU-free topology tests; migration contract suite; fake inference suite; generated OpenAPI is current.

**Rollout/back-compat:** deploy Release N+1 only after all routes/data are converted. If old settings are detected, fail with a conversion command rather than silently ignoring them. Operators remove old secret-store entries only after this release is healthy.

**Risks:** a deployment may skip Release N. The startup error must list detected legacy variables without printing values and link to the conversion procedure.

**Acceptance criteria:** a fresh production Compose deployment with only generic registry/secrets settings passes validation and runs planning/STT with no vendor-specific environment variable.

#### 27. Enforce final provider-neutrality proof

**Dependencies:** 26.

**Files:** scanner and CI workflow from Task 2; docs and release evidence files.

**Migration:** none.

**Required proof commands/checks:**

```bash
# Runtime/config/UI/infra/tests: strict scan; only adapter implementations/docs may match.
python scripts/verify/vendor_neutrality.py --enforce

# Confirm no provider-specific environment variable or secret is in active Compose/config.
grep -RInE 'RUNWAY|OPENROUTER|GROQ|JINA|GOOGLE_VISION|PYANNOTEAI|LALAL' \
  apps/api/app apps/web apps/worker infra .github scripts \
  --exclude-dir='__pycache__' --exclude='*.lock'

# Confirm old persisted JSONB key is not written by current code.
grep -RInE 'runway_task_id|runway_gateway_route|provider="runway"' \
  apps/api/app apps/web packages/shared

# Verify all capability ids and required startup routes are covered.
python -m pytest apps/api/tests -k 'inference or gateway or capability or gpu_free or compose'
```

Historical applied migrations may report the old enum addition and old data key because they are immutable records. The strict proof therefore has two explicit outputs: (1) a zero-finding active-source scan for all current runtime/config/UI/infra/scripts/tests, and (2) a separately labeled historical-migration report. A new non-document/non-adapter match fails CI. If repository policy permits rewriting never-applied migrations before release, those files are included in the strict scan; otherwise they are not silently treated as current coupling.

**Tests:** run the full required CI checks, fake-provider suite with network blocked, Compose contract tests, SQL migration tests, OpenAPI generation check, UI source/browser tests and a no-GPU smoke deployment.

**Rollout/back-compat:** require the proof as the release gate after old settings and adapters are removed. Keep the compatibility converter as a documented artifact outside runtime code only if operators still need it; otherwise delete it.

**Risks:** generated files, docs, fixtures or historical migrations can create misleading grep results. The scanner must print path, line, category and reason for every exception.

**Acceptance criteria:** CI reports zero active-source vendor couplings outside explicitly allowed adapter implementations and docs; all requested capability ids are present; only planning/STT are startup-required; no real key is needed by normal tests; and the no-GPU smoke profile completes.

---

## 5. Migration inventory and ordering

Migrations must be forward-only and independently tested in `supabase/tests/`.

1. **Feature/category quota migration, if needed:** add a neutral category index and compatibility view/query mapping old provider-keyed cost rows to Motionly feature categories. Do not delete historical rows.
2. **Inference operations migration:** create `inference_operations`, RLS, worker grants, idempotency uniqueness, state checks and route-operation uniqueness.
3. **Generated B-roll migration:** add `generated` enum value, backfill stock assets/slots, backfill operation rows from old JSONB, add neutral payload marker and prevent new old-value writes.
4. **Inference registry migration:** create adapter/model/route/secret/version tables, encrypted secret columns, RLS and restricted grants.
5. **Optional indexes:** add only verified missing indexes for route health, quota category lookup and operation polling.

Never edit an applied historical migration to remove an old enum value or old vendor text. New code must stop writing old values before backfill. Migration tests must cover forward application, RLS, old-row reads, backfill idempotency and rolling-deploy ordering.

---

## 6. Rollout and back-compat runbook

### Release N — additive foundation

1. Deploy fake adapter/tests, taxonomy aliases, registry validator and generic secret resolver.
2. Keep existing environment variables and direct providers through the legacy translator.
3. Add `/capabilities` and UI unavailable states.
4. Create `inference_operations` and registry tables, but keep file/legacy source as fallback.
5. Run the converter in dry-run mode and compare route catalogs between old and new configuration.

### Release N+1 — route and data cutover

1. Deploy code that reads neutral generated B-roll fields and writes only neutral fields.
2. Run the generated asset/operation backfill.
3. Enable generic template conformance for object detection, diarization and stems.
4. Switch production to registry + generic secret bundle; leave translator available for rollback.
5. Verify planning/STT required capabilities, optional capability report, route tests, circuit state, quota continuity and no-GPU smoke profile.

### Release N+2 — removal and enforcement

1. Delete transitional provider implementations and direct vendor registration.
2. Delete old settings, Compose secrets, CI secret names and direct health probes.
3. Remove the translator after confirming no old settings remain in deployment manifests.
4. Remove old UI/provider labels and regenerate shared API contracts.
5. Enforce the neutrality scanner and archive the migration evidence.

### Rollback rules

- Roll back application code only to a version that understands the already-applied schema additions.
- Never roll back by deleting `inference_operations`, registry tables or enum values.
- If a remote async submit is uncertain, mark `submit_unknown` and reconcile; do not roll back by resubmitting.
- Keep old payload keys readable until every in-flight operation is terminal and the compatibility window expires.
- Rotate/revoke old secrets only after all services have loaded the generic configuration.

---

## 7. Required test matrix

### Core and schema

- Every capability request/response validates Motionly-owned types.
- Every active route has an adapter, model, revision, pricing/free declaration and valid auth.
- Unknown capability, adapter, mapping function, template field and route reference fails clearly.
- No secret appears in errors, logs, reprs, API responses, OpenAPI examples or telemetry.

### Gateway behavior

- Priority ordering and explicit fallback order.
- Fallback on safe pre-send transient failures.
- No fallback after an async submit may have reached the remote service.
- Required route/revision binding.
- Workspace/monthly/request/feature budget reservation and settlement.
- Quota continuity across provider/route change.
- Redis and in-memory circuit state, cooldown and health reporting.
- Route reload does not interrupt an active call.

### Adapter behavior

- OpenAI-compatible chat, STT, vision, audio, embeddings and image generation.
- Generic HTTP mappings, multipart, aligned arrays, item errors, normalized boxes and usage.
- Async submit/poll/artifacts/cleanup/idempotency.
- OAuth2 and SigV4 auth strategies.
- Private endpoint/no-auth policy and SSRF/IP blocking.
- Fake adapter works with no real keys.

### Data/migration

- Existing phase5 resources and generated B-roll remain readable.
- Old JSONB operation fields dual-read during compatibility.
- `inference_operations` RLS and worker grants.
- Registry secret encryption, AAD, rotation and scope enforcement.
- Generated asset enum/data backfill and idempotency.
- SQL migration contract coverage in `apps/api/tests/test_migration_contract_coverage.py` plus matching `supabase/tests/` files.

### No-GPU and deployment

- `infra/docker-compose.remote-ai.yml` has no required provider-specific secret mounts.
- `GPU_WORKERS_ENABLED=false` excludes all GPU-required routes and starts CPU/remote features.
- CPU matte, stems and sound classification work where configured.
- Planning/STT remote fake routes satisfy startup.
- Missing optional routes never block startup.
- Compose env examples contain only generic registry/secret settings after cutover.

### UI and contracts

- `/capabilities` drives visible disabled/unavailable states.
- Admin cannot expose secrets to browser code.
- Route test displays latency/cost/schema result.
- Shared OpenAPI and generated TypeScript match API routes.
- Generated B-roll uses neutral text and `generated` asset value.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Duplicate paid async submissions | Persist operation before send, idempotency key, commit boundary, `submit_unknown`, original-route polling and no post-send fallback. |
| Provider switch resets quota | Count by Motionly feature/category, not provider label; migrate/read historical usage. |
| Secret leakage through logs or UI | AES-GCM secret store, write-only endpoints, exact-value redactor registration, no request-body logging, fake secret tests. |
| Admin-configured URL SSRF | HTTPS/public-host validation, private network explicit flag, IP/metadata blocking, artifact host allowlist and no template host override. |
| Generic templates become a programming language | Small schema-validated grammar, bounded mappings, named normalization functions, no arbitrary execution. |
| In-flight route removed or edited | Persist immutable route snapshot; block deletion while nonterminal operations exist; status uses snapshot. |
| GPU-free boot accidentally requires a GPU | Route `requires_gpu`, capability startup validation and Compose contract test. |
| Local model license violation | Allowlist license/commercial flags, source URL, verification timestamp and optional block flag. |
| Worker/API registry drift | Registry version and route catalog fingerprint in startup/heartbeat checks. |
| Historical migration grep noise | Separate immutable migration report from active-source strict proof; never hide new coupling in that exception. |
| Backfill races with old writers | Deploy neutral readers/writers first, then backfill, then remove old writers. |
| Overly broad inference scope | Keep unrelated stock, payment, storage and rendering features out of this change except for their existing inference references. |

---

## 9. Final neutrality proof

The release is complete only when all of these are true:

1. `planning.chat` and `transcription.speech_to_text` are the only startup-required capabilities.
2. Every optional capability is represented in `/capabilities` and has a clear UI unavailable state when unconfigured.
3. Adapter selection, route priority, model revision, prices, budgets, auth and mappings are registry/config values.
4. Global secrets are encrypted at rest, referenced by name and absent from all client responses/logs.
5. Async jobs persist `provider_operation_id`, `provider_route_id` and `provider_model_revision`, remain route-affine and never duplicate a submitted job during fallback/retry.
6. The no-GPU Compose profile runs remote/CPU routes without a GPU and a later private compatible endpoint requires no code change.
7. Google Vision, Jina, OpenRouter, Groq, Runway, pyannoteAI and LALAL implementation code/secrets/health checks/UI strings are removed or confined to explicitly documented adapter examples. No Anthropic/OpenAI SDK dependency or direct SDK call exists in core.
8. The fake adapter suite passes without real keys or network access.
9. The active-source neutrality scan passes with zero findings outside explicitly allowlisted adapter implementation files and docs. Historical migrations are reported separately and contain no newly introduced coupling.
10. The exact commit pushed for this plan contains no application implementation; it is only the audited Markdown plan.

The practical release gate is:

```bash
python scripts/verify/vendor_neutrality.py --enforce
python -m pytest apps/api/tests -k 'inference or gateway or capability or gpu_free or compose'
pnpm test
pnpm build
# plus Supabase migration tests and the no-GPU Compose smoke test
```

The scanner output, migration output, route catalog, capability response and no-GPU smoke result are retained as release evidence.