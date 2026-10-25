> **Temporal mTLS/authz release gate:** the custom Temporal server image is never automatically activated without a completed same-VM overlap proof. Follow `docs/ops/temporal-mtls-canary.md`; `temporal_canary_gate.sh` refuses activation/migration when the proof is absent or incomplete. No live canary is claimed by this repository.

# Compose production deployment on one AWS instance

Motionly production has exactly one supported runtime: the digest-only `infra/docker-compose.gpu.yml` stack on the one EC2 instance of the `infra/aws` stack in Mumbai ([AWS infrastructure](aws-infrastructure.md)). Managed-cluster, autoscaling, Railway, direct tag deployment, and VMSS paths are unsupported. A VMSS is explicitly unsupported until media state, PostgreSQL, Redis, and local model/cache storage have an approved shared-state/externalization design, and neither is an autoscaling group.

## One-time host bootstrap

1. Deploy the `infra/aws` stack. Its first-boot script mounts the data volume at `/srv/motionly` and installs Docker, a pinned Docker Compose plugin and Cosign; the AWS CLI and Python 3 come with Amazon Linux 2023. The instance has no SSH: operators use SSM Session Manager and GitHub uses SSM Run Command.
2. Create the SSM SecureString parameters under `/motionly/<environment>/`: `postgres-password`, `redis-password`, `temporal-db-password`, `supabase-jwt-secret`, `supabase-anon-key`, `supabase-service-role-key`, `supabase-worker-key` (the restricted `motionly_worker` JWT), the Realtime secrets (`realtime-db-enc-key`, `realtime-secret-key-base`, `realtime-metrics-jwt-secret`), `admin-api-token`, `worker-api-token`, `metrics-bearer-token`, `ai-secrets-bundle` (the JSON object of inference route credentials referenced by the registry as `bundle:<key>`; see the [registry guide](../ai-registry/README.md)), `aws-inference-access-key-id` and `aws-inference-secret-access-key` (the dedicated Bedrock IAM user's static access keys, referenced by a Bedrock adapter as `file:aws_inference_access_key_id` / `file:aws_inference_secret_access_key`; both are fetched with `optional`, so a deployment without any Bedrock route needs neither parameter — see `docs/aws`), `edge/origin-verify` (the CloudFront origin secret; `edge/origin-verify-previous` only during a rotation), the base64-encoded Temporal certificate and key parameters (`temporal-ca-cert` through `temporal-diagnostics-key`), and `registry-read-token`, a read-only GHCR token. The instance role can read only this path.
3. From a checkout of the release tag on the instance, run `sudo scripts/deploy/bootstrap_compose_host.sh`. It installs `/opt/motionly/agent/bin/apply-release` and creates `/etc/motionly/host.env` if absent.
4. Fill only non-secret values in `/etc/motionly/host.env` (`root:root`, `0600`): `MOTIONLY_ENVIRONMENT`, `MOTIONLY_RELEASE_BUCKET` (the stack's `ReleasesBucketName` output), `MOTIONLY_COMPUTE_PROFILE` (the stack's `ComputeProfile`), `MOTIONLY_REGISTRY_USERNAME`, and `MOTIONLY_APP_DOMAIN`, the CloudFront domain from the stack's `PublicOrigin` output. That domain is the one public origin: the agent writes `PUBLIC_ORIGIN=https://<app domain>`, and the ingress routes `/api` and `/supabase` on it.

The agent reads the parameters only through the instance role, writes them to per-release `0700` directories as read-only `0444` files, and mounts them as Compose secrets. No secret is ever an SSM Run Command argument.

## Inference registry

The stack reads its AI routes from `AI_REGISTRY_FILE=/opt/motionly/ai-registry/registry.json`. Compose bind-mounts `infra/ai-registry/` read-only at that path, so the release bundle carries whatever that directory holds. The registry contains secret references only, never values.

1. Write the production registry as `infra/ai-registry/registry.json` on the release branch. It needs routes for `planning.chat` and `transcription.speech_to_text`, with pricing, a pinned or opted-in revision and `data_processor` metadata. Follow the [registry guide](../ai-registry/README.md).
2. Validate it before tagging: `cd apps/api && python -m app.ops.validate_inference_registry ../../infra/ai-registry/registry.json --app-env production`.
3. Store the credentials it references in the `ai-secrets-bundle` SSM parameter as one JSON object.

Production refuses to start without a valid registry. No GPU service is required: the GPU-free remote overlay serves the remaining capabilities from remote or CPU routes, and its `demucs-worker` (Compose profile `local-stems`) is needed only when the registry includes the CPU Demucs route from `infra/ai-registry/cpu-local.json`.

## CPU-only production (GPU-free topology)

A `ComputeProfile=cpu` host (`m7i.2xlarge`) can also run CPU-only **production**, not only rehearsals: the signed release agent deploys `infra/docker-compose.gpu.yml` plus the GPU-free overlay `infra/docker-compose.remote-ai.yml`, routing every GPU capability to a remote Bedrock route and keeping CPU work local on `cpu-vision-worker` and `demucs-worker`. Two optional, non-secret host settings in `/etc/motionly/host.env` turn it on; both default to today's behaviour, so an existing host and already-published releases are unchanged until they opt in (`infra/compose-host/host.env.example`):

- `MOTIONLY_CPU_TOPOLOGY` — `base` (default) deploys only `docker-compose.gpu.yml`, exactly as before. `gpu-free` adds the overlay; it requires `MOTIONLY_COMPUTE_PROFILE=cpu`, a release bundle that carries the overlay, and a signed manifest that lists `MOTIONLY_CPU_VISION_WORKER_IMAGE`. The agent refuses an invalid value or an unsupported release before reading any secret (`refuse` exits 78 for a bad host setting; `validate_release_topology` exits 65 for a release that lacks the overlay or the image).
- `MOTIONLY_LOCAL_STEMS` — `disabled` (default) leaves `audio_demucs` on `cpu-vision-worker`. `enabled` starts the Demucs worker (`--profile local-stems`) and appends `CPU_VISION_WORKER_QUEUES=visual_analysis,transcription,matte_extraction` to the release's `compose.env`, so stems run on the dedicated `demucs-worker`. Set `enabled` when the registry serves stems through the CPU Demucs route (the shipped `infra/ai-registry/registry.json` does). `enabled` requires `MOTIONLY_CPU_TOPOLOGY=gpu-free`.

The agent records the resolved topology in each release's `topology.env` (mode 0600) next to its `compose.env`. The preflight success line names the topology (for example `topology gpu-free, local stems enabled`). Because the recorded topology travels with the release, rollback and restore reuse exactly what was applied. The signed-release path still refuses `MOTIONLY_COMPOSE_OVERLAY` outside rehearsal; the GPU-free overlay is a bundled file selected by the topology setting, not an operator override. The end-to-end first paid test walks through this on Bedrock (`docs/aws/first-paid-test.md`).

## Release flow

1. A `v*` tag runs the supply-chain workflow. It builds the linux/amd64 components, scans and signs each pushed `ghcr.io/...@sha256:...` image, and creates a signed release manifest mapping every Compose image variable to those digests.
2. `deploy-aws.yml` runs after a successful tag supply-chain run. It assumes the stack's `GitHubDeployRole` through GitHub OIDC and uploads the bundle, certificate, signature and, last, the signed manifest to the releases bucket with conditional writes, so a published release is never overwritten.
3. It then runs `apply-release --release-id <sha> --preflight` on the instance over SSM Run Command (`scripts/deploy/run_on_instance.sh`, which waits for the command and fails unless it succeeded). Preflight validates the S3 objects, manifest digests, image registry, bundle layout and Cosign identity without changing containers or reading application secrets.
4. The `production` GitHub Environment must have required reviewers; the deploy role trusts only that environment. After approval, the workflow passes only the release SHA to the instance. The agent validates the signed manifest, reads the SSM parameters, pulls the images by digest, brings up the topology (`--profile gpu` for `ComputeProfile=gpu`) and waits until every service reports healthy; the long-running services all have health checks, so a crash-looping service fails the release.

Every `docker compose up` runs the one-shot `db-migrate` service before any application service starts. It applies pending `supabase/migrations` files in version order under an advisory lock and records each version in `supabase_migrations.schema_migrations`, the table the Supabase CLI uses (see `infra/db/migrate.sh`). If it fails, no application service starts. Postgres initializes an empty volume with the Supabase image's own init scripts plus `infra/db/roles.sql`, which sets the Auth, REST and Storage login passwords from the `postgres_password` secret.

The workflow needs three non-secret GitHub variables in the `production` environment: `AWS_DEPLOY_ROLE_ARN` (stack output `GitHubDeployRoleArn`), `AWS_RELEASE_BUCKET` (`ReleasesBucketName`) and `AWS_INSTANCE_ID` (`InstanceId`).

## Release rehearsal

`pnpm verify --stage aws` rehearses this path locally (`scripts/release/rehearse_release.py`). It runs the real agent against [moto](https://github.com/getmoto/moto) standing in for S3 and SSM, a local registry holding the CPU-stage images, and a release signed with a throwaway Cosign key. It publishes and applies two releases, checks the public origin after each, breaks a third so it never becomes healthy and checks that the agent restores the second (still `current`) and the origin keeps answering, and tears everything down. It writes `test-results/aws/rehearsal.json`; a failed run also keeps each service's state and recent log in `test-results/aws/rehearsal-services/` (throwaway rehearsal secrets only). It needs Docker and the CPU-stage images, which the stage builds first, so CI runs it in its own `aws-release-rehearsal` job. In rehearsal the agent permits endpoint overrides, a loopback registry, a key-based signature and the CPU-stage overlay (labelled test fixtures, no GPU); a production or staging host refuses each of them.

## Rollback and evidence

The host records `current` and `previous` release directories plus copies of their manifests under `MOTIONLY_RELEASE_ROOT`. If activation health checks fail, the agent automatically activates the previous digest manifest. Each release also keeps its own `topology.env`, so an automatic restore or an operator rollback brings back the previous release with the `MOTIONLY_CPU_TOPOLOGY` / `MOTIONLY_LOCAL_STEMS` it was applied with (a GPU-free release rolls back to the previous GPU-free or base release exactly as recorded). An operator can request the same rollback over SSM Run Command or Session Manager:

```sh
sudo /opt/motionly/agent/bin/apply-release --rollback
```

Do not use `docker compose pull` with tags, edit a release env file by hand, or reset volumes during rollback. Capture `docker compose logs`, the signed release manifest/certificate/signature, CloudTrail records of the SSM commands and parameter reads, and GitHub Environment approval history in the incident record.

## Secrets and access boundaries

- The GitHub deploy role can only write release objects to the releases bucket and run `AWS-RunShellScript` on this instance.
- The instance role reads release objects and `/motionly/<environment>/*` parameters; it cannot write releases or delete backups.
- The registry token is read-only and never appears in workflow logs or command parameters.
- API and worker credentials are file-backed `/run/secrets/*`; only unavoidable Supabase/Redis bootstrap fields are written to the protected per-release env file.
- Keep one instance: local PostgreSQL, Redis, storage, model cache, and Compose volumes live on the instance's data volume by design.
