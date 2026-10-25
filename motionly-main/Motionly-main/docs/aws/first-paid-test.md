# First paid test

This runbook takes the Bedrock deployment from "configured" to "one short clip and one 5 s video
proven end to end", on a controlled budget, with a clear rollback. Do every step in order; each
preflight gate is cheap and catches a class of failure that is expensive to debug after you have
spent money.

Prerequisites: you have completed steps 1–7 of the
[AWS deployment order of operations](README.md) — the `motionly-inference` IAM user and policy,
the two S3 buckets, the BDA project, confirmed model access (including an admin's first Luma Ray 2
invocation), the two SSM SecureString parameters, a filled registry, and the AWS Budget. Nothing
here contains a real account id, ARN, key, bucket name or email; replace every placeholder.

## 1. Preflight gates (no spend)

Run all four before you start the stack. If any fails, stop and fix it.

### 1a. Registry preflight in production mode

The preflight CLI fails in production if any placeholder or pricing marker is still unfilled, and
on any schema error:

```bash
cd apps/api
.venv/bin/python -m app.ops.preflight_inference_registry \
  ../../infra/ai-registry/registry.json --app-env production
```

Exit code 0 means every `<...>` placeholder and every `FILL FROM AWS PRICING PAGE` marker has
been replaced and the registry validates. A non-zero exit lists each remaining placeholder with
its JSON path. (See [Inference registry](../ai-registry/README.md) for the preflight CLI.)

### 1b. IAM policy validation (Access Analyzer)

```bash
aws accessanalyzer validate-policy \
  --policy-type IDENTITY_POLICY \
  --policy-document file://iam-inference-policy.json
```

Resolve any `ERROR` or `SECURITY_WARNING` findings. This also exercises the resource-level scoping
for `bedrock:GetAsyncInvoke` and `bedrock:GetDataAutomationStatus`, which could not be confirmed
from the Service Authorization Reference this session — see the Unverified facts in
[README](README.md) and [iam-inference-policy.md](iam-inference-policy.md).

### 1c. Credential isolation test

Confirm that only the API and the inference workers receive the AWS secrets and that no infra or
media-only service does:

```bash
cd apps/api
APP_ENV=test .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_aws_credential_isolation.py
```

### 1d. SSM parameters present

The two SecureString parameters must exist so the release agent can fetch them (both are fetched
with `optional`, so a deployment without Bedrock still starts — but this test needs them):

```bash
aws ssm get-parameter \
  --name "/motionly/<environment>/aws-inference-access-key-id" \
  --with-decryption --query 'Parameter.Name' --output text
aws ssm get-parameter \
  --name "/motionly/<environment>/aws-inference-secret-access-key" \
  --with-decryption --query 'Parameter.Name' --output text
```

Print only the parameter **name**, never the value. (Storage of these parameters without echoing
the secret is covered in [iam-inference-policy.md](iam-inference-policy.md) and the
[Compose VM deployment](../ops/compose-vm-deployment.md) secrets list.)

## 2. Start the stack (GPU-free, with Demucs) via the signed release

Run the first paid test on an `m7i.2xlarge` instance with `ComputeProfile=cpu`. The CPU-only
topology is the base `infra/docker-compose.gpu.yml` stack plus the GPU-free overlay
`infra/docker-compose.remote-ai.yml`, which routes every GPU feature to a remote Bedrock route and
keeps every CPU feature local.

### Deploy with the release agent (recommended)

The signed release agent (`scripts/deploy/apply_release.sh`) now deploys the GPU-free overlay when
the host opts in. Two optional, non-secret host settings drive it; both default to today's
behaviour, so existing hosts and already-published releases are unchanged unless they set them
(`scripts/deploy/apply_release.sh:69-84`; `infra/compose-host/host.env.example`):

```ini
# /etc/motionly/host.env (root:root, 0600). ComputeProfile=cpu on an m7i.2xlarge.
MOTIONLY_COMPUTE_PROFILE=cpu
MOTIONLY_CPU_TOPOLOGY=gpu-free
MOTIONLY_LOCAL_STEMS=enabled
```

- `MOTIONLY_CPU_TOPOLOGY=gpu-free` adds `infra/docker-compose.remote-ai.yml` on top of
  `docker-compose.gpu.yml`. It requires `MOTIONLY_COMPUTE_PROFILE=cpu`, a release bundle that
  carries the overlay, and a signed manifest that lists `MOTIONLY_CPU_VISION_WORKER_IMAGE`; the
  agent refuses the combination otherwise before reading any secret
  (`scripts/deploy/apply_release.sh:69-84` and the `validate_release_topology` function,
  exit 78 for a bad host setting, exit 65 for an unsupported release).
- `MOTIONLY_LOCAL_STEMS=enabled` starts the Demucs worker (`--profile local-stems`) and appends
  `CPU_VISION_WORKER_QUEUES=visual_analysis,transcription,matte_extraction` to the release's
  `compose.env`, so `cpu-vision-worker` gives up `audio_demucs` and the dedicated `demucs-worker`
  serves stems. Set it because the shipped registry serves stems through the local CPU Demucs
  route (`write_release_topology` in `scripts/deploy/apply_release.sh`). `enabled` requires
  `MOTIONLY_CPU_TOPOLOGY=gpu-free`.

The agent records the resolved topology in each release's `topology.env` (mode 0600) so that
rollback and restore reuse exactly what was applied; no secret value ever enters the agent's argv
or output.

Bring the stack up through the normal signed flow:

1. Tag a release (`v*`). The supply-chain workflow builds the images — including the
   `cpu-vision-worker` component — signs them, and generates a signed manifest that now carries the
   optional `MOTIONLY_CPU_VISION_WORKER_IMAGE` entry and its SBOM. The release bundle carries
   `infra/docker-compose.remote-ai.yml`.
2. `deploy-aws.yml` uploads the bundle, runs preflight on the instance, then waits for the
   `production` environment approval. Preflight validates the GPU-free topology against the signed
   release and prints the topology in its success line, for example:
   `Validated signed release <sha> (topology gpu-free, local stems enabled) and Temporal canary
evidence; no containers or secrets were changed.`
   (`scripts/deploy/apply_release.sh`, the `--preflight` branch).
3. Approve the deploy. The agent validates the signed manifest, reads the SSM parameters, pulls the
   images by digest, and brings up `docker-compose.gpu.yml` + `docker-compose.remote-ai.yml` with
   `--profile local-stems` (no `--profile gpu`, because `ComputeProfile=cpu`).

### Fallback: operator-run Compose

> **Fallback only.** Use the signed-release flow above unless you are debugging on the host. This
> operator-run Compose command reproduces the same topology by hand, reusing the release's env file
> and digests, over SSM Session Manager.

The overlay's `cpu-vision-worker` serves the GPU services' and the Demucs worker's queues on CPU.
For this test the Demucs stems route stays on its own `demucs-worker` (Compose profile
`local-stems`), so remove `audio_demucs` from the cpu-vision worker's queues and keep the
`local-stems` profile:

```bash
# On the host, from the release checkout. <compose.env> is the release env file
# the agent wrote; it already carries the digest-pinned image variables and the
# non-secret host inputs. SECRETS_DIR points at the release's 0700 secret dir.
MOTIONLY_CPU_VISION_WORKER_IMAGE="<digest-pinned ghcr cpu-vision image>" \
CPU_VISION_WORKER_QUEUES="visual_analysis,transcription,matte_extraction" \
VISUAL_PROVIDER="remote" \
AUDIO_DIARIZATION_PROVIDER="disabled" \
docker compose --project-name motionly --env-file <compose.env> \
  --profile local-stems \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.remote-ai.yml \
  up -d --wait
```

### The env switches, verified in code

Three switches decide which worker serves which capability on this overlay. The Bedrock routes are
reached through the gateway regardless of switch; the switches decide **which worker runs the job
and whether a GPU-only local provider is required**.

- `VISUAL_PROVIDER=remote` — visual analysis (and its object detection, captions and keyframe
  embeddings) runs on `cpu-vision-worker` using CPU signals plus the registry's
  `vision.object_detection` and `vision.caption` Bedrock routes, instead of the GPU provider
  (`apps/api/app/core/config.py:338`;
  `apps/api/app/workers/visual_job.py:63`, `:96-98`;
  `apps/api/app/services/ai_features.py:51-61`, which reports `visual_*` as
  `execution: cpu_worker, switch: VISUAL_PROVIDER` when `remote`). The overlay defaults this to
  `remote` (`infra/docker-compose.remote-ai.yml:52`). Setting `VISUAL_PROVIDER=local_ml` would
  require a GPU worker, which production's GPU-free check rejects
  (`apps/api/app/core/production.py:115-121`).
- `AUDIO_DIARIZATION_PROVIDER=disabled` — the **local** pyannote diarization provider needs a GPU
  worker, so a GPU-free deployment disables it (`apps/api/app/core/config.py:299-300`; overlay
  default `disabled`, `infra/docker-compose.remote-ai.yml:59`). Diarization is then served by the
  registry `audio.diarization` Bedrock route (BDA), not by a vendor switch. Do **not** set
  `local_ml` here on a CPU host.
- `CPU_VISION_WORKER_QUEUES` — the queues `cpu-vision-worker` consumes. The overlay default is
  `visual_analysis,transcription,matte_extraction,audio_demucs`
  (`infra/docker-compose.remote-ai.yml:112`). For this test set it to
  `visual_analysis,transcription,matte_extraction` so stems stay on the dedicated `demucs-worker`
  (started by `--profile local-stems`); `transcription` carries the BDA transcription **and**
  diarization work.

### Which worker runs each capability

| Capability                     | Queue                          | Service in this topology                                                          | Route                                       |
| ------------------------------ | ------------------------------ | --------------------------------------------------------------------------------- | ------------------------------------------- |
| `planning.chat`                | `planning`                     | `planning-worker` (`infra/docker-compose.gpu.yml:835-846`)                        | `bedrock-planning-chat` (Converse)          |
| `transcription.speech_to_text` | `transcription`                | `cpu-vision-worker`                                                               | `bedrock-transcription` (BDA)               |
| `audio.diarization`            | `transcription`                | `cpu-vision-worker`                                                               | `bedrock-diarization` (BDA)                 |
| `vision.object_detection`      | `visual_analysis`              | `cpu-vision-worker`                                                               | `bedrock-object-detection` (Converse)       |
| `vision.caption`               | `visual_analysis`              | `cpu-vision-worker`                                                               | `bedrock-caption` (Converse)                |
| `embeddings.text`              | B-roll ranking (planning path) | `planning-worker` / API                                                           | `bedrock-embeddings-text` (Titan)           |
| `video.generation.submit`      | `provider_calls`               | `provider-worker` (`infra/docker-compose.gpu.yml:821-832`)                        | `bedrock-video-generation` (Luma Ray async) |
| `audio.stem_separation`        | `audio_demucs`                 | `demucs-worker` (`--profile local-stems`, `infra/docker-compose.gpu.yml:805-817`) | `local-stem-separation` (CPU, free)         |

The `provider-worker`, `planning-worker` and `cpu-vision-worker` all mount the two AWS secret
files (via the `&inference-worker-secrets` anchor and the overlay's `secrets:` on
`cpu-vision-worker`); `demucs-worker` and every other media/infra service do not.

## 3. Run one short clip and one 5 s video

Put one very short clip (5–10 s with clear speech and at least two speakers, so diarization has
something to label) through the pipeline, and submit one 5 s video. Watch each capability's result
and where to see it.

| Capability       | Expected result                                                | Where to see it                                                                                                                                                                   |
| ---------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| planning         | A plan is generated for the clip.                              | Plan job completes; `/health/dependencies` shows `bedrock-planning-chat` healthy.                                                                                                 |
| transcription    | Transcript text for the clip.                                  | Transcription job output; BDA job reaches `Success` in `get-data-automation-status`.                                                                                              |
| diarization      | Speaker labels `spk_0..spk_N` on the transcript segments (F6). | Transcript segments carry speaker ids; a job with no speaker labels fails as a malformed response (confirm speaker labeling is enabled on the BDA project).                       |
| object detection | Detected objects on sampled keyframes.                         | Visual analysis manifest; `bedrock-object-detection` healthy.                                                                                                                     |
| captions         | Keyframe captions.                                             | Visual analysis manifest; `bedrock-caption` healthy.                                                                                                                              |
| embeddings       | B-roll ranking uses vectors (not the lexical fallback).        | B-roll ranking output; `bedrock-embeddings-text` healthy.                                                                                                                         |
| stems            | Separated stems.                                               | Stems job output on `demucs-worker` (local, no AWS cost).                                                                                                                         |
| video            | A 5 s MP4 generated by Luma Ray 2.                             | `video.generation.submit` returns a job; poll with `VideoGenerationPollRequest`; the finished MP4 is served via a short-lived presigned `https://` URL (`presign_expires_s` 900). |

Keep inputs tiny: the first-test caps are small on purpose (planning 50/day, video 3/day — see
[Cost guardrails](cost-guardrails.md)). The video route's daily cap is 3, so you get at most three
submit attempts in a UTC day before `route_hard_cap_exhausted`.

## 4. Compare actual cost with the prediction

The next day, compare real spend with the app's prediction:

- **Cost Explorer** (lags ~24 h): group by service. Bedrock (Nova, Titan, BDA) appears under
  Amazon Bedrock. **Luma Ray 2 appears under the model provider / AWS Marketplace, not under
  Amazon Bedrock** (F8), so include Marketplace when you total video cost.
- **AWS Budgets** alert emails to `<ALERT_EMAIL>` (lagging, 8–12 h, F12).
- **App prediction**: the registry pricing fields you filled drive the per-route estimate; compare
  against the `budget.monthly_usd` per route and the hard-cap counts consumed.

If actual materially exceeds the prediction, re-check the pricing values you filled and the number
of invocations (Cost Explorer usage type) before raising any cap.

## 5. Rollback

If anything goes wrong, roll back in this order (fastest kill switch first):

1. **Deactivate the inference user's access key** — the single fastest stop. Every container reads
   the same two secret files; without valid credentials the adapter fails closed with
   `provider_auth_failed` and sends no HTTP request. (`aws iam update-access-key
--access-key-id <id> --status Inactive --user-name motionly-inference`; see
   [iam-inference-policy.md](iam-inference-policy.md).)
2. **Disable routes / restore the previous registry** — set `"enabled": false` on the offending
   routes, or restore the previous `infra/ai-registry/registry.json`, and redeploy.
3. **Stop the stack** — `docker compose --project-name motionly ... down` (same overlay and
   profile flags as the up command). Note: turning a Compose profile off later (for example moving
   a capability back to a GPU worker) does not stop the service that profile started — Compose
   leaves the old container running. Stop it explicitly, for example
   `docker compose --project-name motionly ... rm -sf demucs-worker`.
4. **Empty the buckets** — delete staged `bda/` objects and generated `video/` objects
   (the lifecycle rules also expire them: `bda/` after 1 day, `video/` after 7 days — see
   [S3 buckets](s3-buckets.md)).
5. **Delete the SSM parameters** — remove
   `/motionly/<environment>/aws-inference-access-key-id` and
   `/motionly/<environment>/aws-inference-secret-access-key` if you are tearing the test down.
6. **Release rollback** — if the signed release itself is the problem, roll the app back with the
   agent: `sudo /opt/motionly/agent/bin/apply-release --rollback`. The agent restores the previous
   release and reuses that release's recorded `topology.env` (its `MOTIONLY_CPU_TOPOLOGY` /
   `MOTIONLY_LOCAL_STEMS`), so a GPU-free release rolls back to the previous GPU-free (or base)
   release exactly as it was applied (see [Compose VM deployment](../ops/compose-vm-deployment.md)).

Deactivating the key does not refund Luma Marketplace charges for an in-flight job; it only
prevents new invocations.

## 6. Denied / throttled triage

Bedrock runtime errors and their HTTP status (F14,
<https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModel.html>):

| Error                           | HTTP | Likely cause and fix                                                                                                                                                                                               |
| ------------------------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AccessDeniedException`         | 403  | IAM policy missing an action/resource, or model access not granted. Check CloudTrail `errorCode: AccessDenied` for the exact action and ARN, then compare with [iam-inference-policy.md](iam-inference-policy.md). |
| `ResourceNotFoundException`     | 404  | Wrong region or profile, a disabled model, or Nova Reel (EOL — use Luma Ray 2 in us-west-2, F7). Confirm region/profile match the registry.                                                                        |
| `ThrottlingException`           | 429  | Rate limit. Retry with backoff or request a Service Quotas increase.                                                                                                                                               |
| `ModelNotReadyException`        | 429  | Model warming up; retry.                                                                                                                                                                                           |
| `ValidationException`           | 400  | Malformed request body or unsupported parameter.                                                                                                                                                                   |
| `ServiceQuotaExceededException` | 400  | Account/region quota exceeded; raise it in Service Quotas.                                                                                                                                                         |
| `ModelTimeoutException`         | 408  | Provider timed out; retry.                                                                                                                                                                                         |

Triage checklist:

- **CloudTrail** — look up the `errorCode` (e.g. `AccessDenied`) and the `eventName` to see the
  exact action and resource the call used; match it against the scoped IAM policy.
- **Region / profile mismatch** — Nova via `apac.*` profiles in ap-south-1 needs the foundation
  model granted in every F3 destination region (F2/F3); BDA needs the APAC profile in every F5
  region; Luma Ray 2 and its output bucket must both be us-west-2 (F8). A mismatch surfaces as
  `AccessDenied` or `ResourceNotFound`.
- **Marketplace subscription** — Luma Ray 2 is a Marketplace model; the admin's first invocation
  starts the subscription (F9). If video fails with access errors on the inference user, confirm
  the admin has performed the first invocation (the inference user has no Marketplace permissions
  by design).
- **Bucket policy** — a `Deny` in the bucket policy that does not carry the caller's
  `aws:PrincipalArn` through BDA/async S3 access would block staging/output. This is UNVERIFIED
  (F10); test it and see [S3 buckets](s3-buckets.md).
- **Service Quotas** — check Bedrock and BDA quotas in the relevant regions if throttled.
- **App state** — `GET /health/dependencies` shows each route's circuit state and
  `route_hard_cap_exhausted`. An exhausted hard cap, not AWS, is the cause when a capability stops
  with that code (see [Cost guardrails](cost-guardrails.md)).

## 7. Pass/fail checklist

- [ ] 1a preflight exits 0 in production mode (no placeholders, no pricing markers, schema valid).
- [ ] 1b Access Analyzer reports no errors on the IAM policy.
- [ ] 1c `test_aws_credential_isolation.py` passes.
- [ ] 1d both SSM parameters resolve (name only printed).
- [ ] Preflight succeeds and its success line reports `topology gpu-free, local stems enabled`
      (or the stack is brought up with the operator-run overlay + `--profile local-stems` fallback
      command).
- [ ] Stack reaches healthy with the GPU-free overlay and the `local-stems` profile.
- [ ] planning: a plan is produced.
- [ ] transcription: transcript text produced; BDA job `Success`.
- [ ] diarization: speaker labels present on segments.
- [ ] object detection: objects on keyframes.
- [ ] captions: keyframe captions produced.
- [ ] embeddings: B-roll ranking uses vectors.
- [ ] stems: separated stems produced on `demucs-worker` (no AWS cost).
- [ ] video: a 5 s MP4 generated and downloadable via the presigned URL.
- [ ] Actual cost (Cost Explorer + Marketplace for Luma) is within the prediction, or the gap is
      explained.
- [ ] No unexpected `AccessDenied` in CloudTrail.
- [ ] Rollback steps are understood and the key-deactivation command is to hand.
