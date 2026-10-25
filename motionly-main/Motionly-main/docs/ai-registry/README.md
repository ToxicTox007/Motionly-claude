# Inference registry

Every AI capability in Motionly is served by a route in the inference registry, a JSON file the operator writes. Application code never names a provider: it asks for a capability (`planning.chat`, `audio.stem_separation`, …), and the registry decides which adapter, model and credential serve it. Adding or switching a provider is a configuration change, with no code change.

The design and its rules are in the [implementation plan](../provider-neutral-inference-implementation-plan.md); this page is the operator guide.

## File format (version 1)

```json
{
  "version": 1,
  "adapters": [
    {
      "id": "…",
      "type": "openai_compatible",
      "base_url": "https://…",
      "auth": { "type": "bearer", "secret_ref": "bundle:…" },
      "data_processor": { "…": "…" }
    }
  ],
  "models": [
    {
      "id": "…",
      "model_id": "…",
      "revision": "…",
      "licence": "…",
      "commercial_use": true
    }
  ],
  "routes": [
    {
      "id": "…",
      "capability": "planning.chat",
      "adapter_id": "…",
      "model_id": "…",
      "model_revision": "…",
      "pricing": { "status": "priced" }
    }
  ]
}
```

- **Adapters** describe an endpoint: `type` (`openai_compatible`, `azure_openai`, `generic_http`, `async_job`, `local`, `fake`), `base_url`, `network` (`public` or `private`), `auth`, `timeout_s`, optional `health` probe and `data_processor` metadata.
- **Models** record identity and licence: external `model_id`, pinned `revision`, `licence`, `commercial_use`, source URL and, for downloaded weights, `weights_sha256`.
- **Routes** bind a capability to an adapter and model, with `priority`, `pricing`, `budget` (feature, optional hard cap), `limits`, `requires_gpu`, `enabled` and, for `generic_http` and `async_job`, a `mapping`.

Unknown keys are errors. [`docs/ai-registry-example.json`](../ai-registry-example.json) is a complete, production-valid example for planning and transcription.

## Validation rules

Each rule yields an error (the route is dropped) or a warning (the route stays, and the warning is logged and shown in `/health/dependencies`). Startup fails only if `planning.chat` or `transcription.speech_to_text` has no valid enabled route. A route is **self-hosted** when its adapter is `local` or declares `network: private`; every other route is **remote**.

| Rule             | Self-hosted                                                                          | Remote                                                                               |
| ---------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| Revision         | Immutable pin required; `latest` and `provider-managed` are errors                   | Pin, or `provider-managed` with `allow_provider_managed_revision: true` (warning)    |
| Licence          | Required (error); non-commercial warns, or errors with `AI_BLOCK_NONCOMMERCIAL=true` | Optional (warning)                                                                   |
| `data_processor` | Not required                                                                         | Warning in development, error in production                                          |
| Auth             | `none` allowed                                                                       | Auth with a secret reference required                                                |
| Pricing          | Priced, or `explicitly_free: true`                                                   | Priced, `explicitly_free: true`, or `pricing.status: unknown` with `budget.hard_cap` |
| `fake` adapter   | Error in production                                                                  | Error in production                                                                  |
| `requires_gpu`   | Dropped when `GPU_WORKERS_ENABLED=false`                                             | —                                                                                    |

Validate a registry before deploying it:

```bash
cd apps/api
python -m app.ops.validate_inference_registry /path/registry.json --app-env production
python -m app.ops.validate_inference_registry /path/registry.json,../../infra/ai-registry/cpu-local.json --app-env production
```

The command exits 1 only on errors and never reads or prints secret values.

## Secrets

A route carries a secret **reference**, never a value:

- `file:<name>` reads `<AI_SECRETS_DIR>/<name>` (default `/run/secrets`). Path traversal and symlinks that leave the directory are rejected.
- `bundle:<key>` reads one key from the JSON object at `AI_SECRETS_BUNDLE_FILE`. Production Compose mounts this bundle as the `ai_secrets_bundle` secret.
- `env:<NAME>` is for development only and is rejected in production.

Resolved values are registered with the log redactor before first use. Never commit a bundle or a secret file. See [Runtime secrets](../security/secrets.md).

## Combining fragments

`AI_REGISTRY_FILE` takes one path or a comma-separated list. Fragments are merged in order, and an id defined twice is an error (`registry_fragment_collision`). Shipped fragments:

- [`infra/ai-registry/cpu-local.json`](../../infra/ai-registry/cpu-local.json): CPU matte, Demucs stems and YAMNet sound classification.
- [`infra/ai-registry/gpu-local.json`](../../infra/ai-registry/gpu-local.json): local GPU models; dropped unless GPU workers are enabled.
- [`infra/ai-registry/cpu-stage.json`](../../infra/ai-registry/cpu-stage.json): fake routes for the CPU stage.

Without `AI_REGISTRY_FILE`, production and staging refuse to start. Local and test environments use a built-in development registry with fake planning and transcription routes.

## Enabling a capability

Copy a route from [`examples/`](examples/) into your registry, set its `base_url`, `secret_ref`, pricing and `data_processor`, and validate. The examples cover object detection, diarization and stem separation, each with a second, differently shaped provider. Their `mapping` blocks use the template language, whose grammar is frozen (plan Section 2.10): a provider that needs a construct the grammar lacks needs a plan change, not new grammar in code.

## Live runs

Before a vendor adapter is deleted, its template route must pass one live run against the real service. Results are recorded in [`live-runs.md`](live-runs.md). The `Live Provider Contracts` workflow runs them on manual dispatch only, with explicit authorization, an optional capability list, and two GitHub secrets: `MOTIONLY_LIVE_REGISTRY` (the registry JSON) and `MOTIONLY_LIVE_SECRETS_BUNDLE` (the secrets bundle JSON).

## Runtime visibility

- `GET /capabilities` (public): per capability, availability, a machine-readable reason, async flag, compute requirement and route count. No URLs, models or provider labels.
- `GET /public/data-processors` (public): the processors of enabled remote routes, rendered on the privacy page.
- `GET /health/dependencies` (authenticated): the `inference` entry lists validation warnings, dropped routes, circuit state per route and the number of operations stuck in `submit_unknown`.

## Running your own models

You can serve any capability with models you run yourself — on an operator GPU
in-process, or behind a self-hosted endpoint. A local GPU deployment stays a
first-class option and only gets more flexible: a local route can serve any
capability a runner supports, and several local routes may serve one capability
so users can choose between them.

### Local GPU via `local` routes and `--profile gpu`

The shipped [`infra/ai-registry/gpu-local.json`](../../infra/ai-registry/gpu-local.json)
fragment defines `local` routes for embeddings, captions, object detection,
diarization and voice emotion. Merge it with your registry and start the GPU
Compose profile:

```bash
export AI_REGISTRY_FILE=/path/registry.json,../../infra/ai-registry/gpu-local.json
export GPU_WORKERS_ENABLED=true
docker compose --profile gpu up
```

`requires_gpu` routes are dropped (with a warning) when `GPU_WORKERS_ENABLED=false`,
so the same registry boots on a CPU-only host without them. Model prefetch
downloads every enabled local model — including `transformers_pipeline` models —
and verifies `weights_sha256` wherever a model record declares it.

A `local` route names a neutral **runner** in `params.runner`. Besides the
model-family runners (`mediapipe_selfie_matte`, `demucs_stems`, `yamnet_sound`,
`clip_embeddings`, …), the generic `transformers_pipeline` runner serves any
Hugging Face pipeline task from configuration alone:

| `params.pipeline_task`         | Capability                                                 |
| ------------------------------ | ---------------------------------------------------------- |
| `image-to-text`                | `vision.caption`                                           |
| `audio-classification`         | `audio.sound_classification` / `audio.emotion`             |
| `automatic-speech-recognition` | `transcription.speech_to_text` (word timestamps)           |
| `object-detection`             | `vision.object_detection`                                  |
| `zero-shot-object-detection`   | `vision.object_detection` (with `params.candidate_labels`) |
| `feature-extraction`           | `embeddings.text`                                          |

The pipeline task and model come from the route and its model record; the
`transformers` library is imported lazily and only in the GPU/ML image, so a
CPU-only deployment that enables no `transformers_pipeline` route never loads it.

### Self-hosted endpoints

A model you run behind an HTTP endpoint is a route with `network: private`. Any
of `openai_compatible`, `huggingface_endpoint`, `generic_http` or `async_job`
can point at an internal host, `auth: none` is allowed, and an `http://` base
URL is permitted because the network is private. The
[`examples/self-hosted/`](examples/self-hosted/) directory has complete,
production-valid examples for a vLLM chat server, an OpenAI-compatible
speech-to-text server, a Text Embeddings Inference (TEI) server and a Text
Generation Inference (TGI) endpoint. The
[`huggingface-endpoint.json`](examples/huggingface-endpoint.json) and
[`aws-bedrock.json`](examples/aws-bedrock.json) examples show the two hosted
adapter types: a dedicated inference endpoint (bearer secret ref, `config.mode`
one of `tgi`/`tei`/`pipeline`) and a managed cloud model service (the IAM
credential chain, so no AI secret ref — `config` carries `region` and `api`).

### Adding a model by configuration

1. Add a **model** record: its external `model_id`, a pinned immutable
   `revision` (and `weights_sha256` for downloaded weights), `licence` and
   `commercial_use`. Self-hosted/local models require a licence.
2. Add an **adapter** record for the endpoint or the `local` runner. Set
   `network: private` for anything you host; add a `config` block for
   `huggingface_endpoint`/`aws_bedrock`.
3. Add a **route** binding the capability to the adapter and model, with
   `pricing` (self-hosted routes are usually `explicitly_free: true`),
   `priority`, `requires_gpu` for GPU runners, and an optional `offering` block
   so users can pick it.
4. Validate before deploying:

   ```bash
   cd apps/api
   python -m app.ops.validate_inference_registry /path/registry.json --app-env production
   ```

No code change is needed: the registry is the only source of routes.
