# Motionly — Complete Deployment Guide

> Everything you need to take this from source to production. Sourced entirely from the project's own infrastructure files.

---

## Table of Contents

1. [Where to Deploy](#1-where-to-deploy)
2. [Architecture Overview](#2-architecture-overview)
3. [Third-Party Accounts You Must Create](#3-third-party-accounts-you-must-create)
4. [All API Keys & Secrets — Complete List](#4-all-api-keys--secrets--complete-list)
5. [Phase 1 — One-Time AWS Infrastructure Setup](#5-phase-1--one-time-aws-infrastructure-setup)
6. [Phase 2 — Supabase Setup](#6-phase-2--supabase-setup)
7. [Phase 3 — GitHub Secrets & Variables](#7-phase-3--github-secrets--variables)
8. [Phase 4 — Bootstrap the EC2 Host](#8-phase-4--bootstrap-the-ec2-host)
9. [Phase 5 — First Release & Deploy](#9-phase-5--first-release--deploy)
10. [Ongoing Release Flow](#10-ongoing-release-flow)
11. [Environment Variables Reference](#11-environment-variables-reference)
12. [Minimal / Budget Setup (No GPU)](#12-minimal--budget-setup-no-gpu)
13. [GPU-free Remote AI Mode](#13-gpu-free-remote-ai-mode)

---

## 1. Where to Deploy

The project is **designed and documented for exactly one deployment target**:

| Concern | Answer |
|---|---|
| **Cloud** | **AWS** — `ap-south-1` (Mumbai) |
| **Compute** | One EC2 instance — `g6.12xlarge` (GPU, production) or `m7i.2xlarge` (CPU-only, rehearsal) |
| **Edge / CDN** | **AWS CloudFront** (no WAF yet) |
| **Database** | **Self-hosted Supabase** (PostgreSQL 17 + GoTrue + Storage + Realtime + PostgREST) — runs inside Docker on the same EC2 instance |
| **Queue broker** | **Redis** — inside Docker on the same instance |
| **Workflow engine** | **Temporal** — inside Docker (self-hosted) or **Temporal Cloud** (simpler, see env vars) |
| **Container registry** | **GitHub Container Registry (GHCR)** |
| **Runtime** | **Docker Compose** (`infra/docker-compose.gpu.yml`) |

> [!IMPORTANT]
> The docs explicitly warn: **no Kubernetes, no Railway, no auto-scaling groups, no VMSS** until shared-state storage is designed. It's a single-VM monolith with all state (PostgreSQL, Redis, media volumes, model cache) on one persistent EBS data volume.

> [!NOTE]
> **Region is `ap-south-1` (Mumbai).** The CloudFormation template, the prefix-list lookup, and the deploy workflow all hard-code this. Changing it requires editing the template and workflow.

> [!NOTE]
> **CPU-only production is supported.** The `m7i.2xlarge` (`ComputeProfile=cpu`) is not only for rehearsals: the signed release can run it in production with the GPU-free remote AI topology (GPU work → remote Bedrock routes, CPU work local). Set `MOTIONLY_CPU_TOPOLOGY=gpu-free` (and `MOTIONLY_LOCAL_STEMS=enabled` when stems run on the local CPU Demucs route) in `/etc/motionly/host.env`; both default to the GPU topology above when unset. See [Section 13](#13-gpu-free-remote-ai-mode).

---

## 2. Architecture Overview

```
                    Internet
                       │
                  CloudFront
              (HTTPS, cached statics)
                       │  X-Motionly-Origin-Verify header
                       │
              EC2 g6.12xlarge (ap-south-1)
              /srv/motionly  (encrypted gp3 EBS)
              ┌────────────────────────────────┐
              │  Caddy (ingress, port 80)       │
              │    /api/*   → api:8000          │
              │    /supabase/* → supabase-gw   │
              │    /*       → web:3000          │
              │                                 │
              │  api        (FastAPI/Python)    │
              │  web        (Next.js)           │
              │  worker     (BullMQ/Node.js)    │
              │    ├─ cpu-ffmpeg-worker         │
              │    ├─ demucs-worker             │
              │    ├─ provider-worker           │
              │    ├─ planning-worker           │
              │    ├─ remotion-worker           │
              │    ├─ gpu-inference-worker      │
              │    └─ matte-worker              │
              │  temporal   (workflow engine)   │
              │  redis      (BullMQ broker)     │
              │  supabase   (postgres/auth/etc) │
              │  payment-gateway (mock, demo)   │
              └────────────────────────────────┘
                       │
              S3 buckets (releases, media, backups)
```

---

## 3. Third-Party Accounts You Must Create

### Required (nothing works without these)

| Service | Purpose | Where to sign up |
|---|---|---|
| **AWS** | EC2, CloudFront, S3, SSM, IAM, CloudFormation | aws.amazon.com |
| **Supabase** | Auth JWT keys + self-hosted config (or cloud project for keys only) | supabase.com |
| **A transcription endpoint** | Speech-to-text with word timestamps. Any OpenAI-compatible `audio/transcriptions` API works | your choice |
| **A planning endpoint** | AI edit planning. Any OpenAI-compatible `chat/completions` API works | your choice |
| **GitHub** | Container registry (GHCR) + CI/CD | github.com |

AI providers are not built in. You choose them and declare them as routes in the inference registry ([registry guide](motionly-main/Motionly-main/docs/ai-registry/README.md)). One provider can serve both planning and transcription.

### Required for premium rendering (B-roll, visual effects)

| Service | Purpose | Where to sign up |
|---|---|---|
| **A video-generation API** (optional) | Generated B-roll, configured as a `video.generation.submit` registry route | your choice |
| **Pexels** | Stock video search | pexels.com/api |
| **Pixabay** | Stock video search (fallback) | pixabay.com/api |

### Optional / advanced

| Service | Purpose |
|---|---|
| **Azure OpenAI** | Supported as an `azure_openai` registry adapter for planning |
| **Temporal Cloud** | Managed workflow orchestration (instead of self-hosted Temporal in Docker) |
| **OTEL-compatible backend** | Observability (e.g. Grafana Cloud, Datadog) |

---

## 4. All API Keys & Secrets — Complete List

These are all the secrets the system expects. They are stored in **AWS SSM Parameter Store** under `/motionly/<environment>/` and are **never** stored in Git or passed as command-line arguments.

### 4.1 — Secrets you generate yourself (random tokens)

| SSM Parameter Name | Description | How to generate |
|---|---|---|
| `postgres-password` | Supabase PostgreSQL superuser password | `openssl rand -hex 32` |
| `redis-password` | Redis AUTH password | `openssl rand -hex 32` |
| `temporal-db-password` | Temporal's own Postgres password (separate DB) | `openssl rand -hex 32` |
| `admin-api-token` | Bearer token for internal admin API routes | `openssl rand -hex 32` |
| `worker-api-token` | Bearer token workers use to call the API | `openssl rand -hex 32` |
| `metrics-bearer-token` | Token for scraping `/metrics` (Prometheus) | `openssl rand -hex 32` |
| `edge/origin-verify` | Secret CloudFront sends to the origin (32–256 chars `A-Za-z0-9_-`) | `openssl rand -base64 32 \| tr -d '/+='` |
| `edge/origin-verify-previous` | **Empty** except during key rotation | Leave empty initially |
| `merchant-api-key` | HMAC key between API and mock payment gateway | `openssl rand -hex 32` |
| `mock-gateway-webhook-secret` | Webhook signing secret for billing callbacks | `openssl rand -hex 32` |

### 4.2 — Supabase keys (from your Supabase project or self-hosted setup)

| SSM Parameter Name | Description |
|---|---|
| `supabase-jwt-secret` | The master JWT secret (GoTrue signs all tokens with this) |
| `supabase-anon-key` | Public anon JWT (safe to expose to browsers) |
| `supabase-service-role-key` | Service-role JWT (full DB access — keep secret) |
| `supabase-worker-key` | **Restricted worker JWT** — role claim = `motionly_worker` (applied by migration `202610190501`). NOT the service-role key. Workers use this, not the service-role key. |
| `realtime-db-enc-key` | Realtime's 16-byte encryption key | `openssl rand -hex 16` |
| `realtime-secret-key-base` | Realtime Phoenix endpoint secret | `openssl rand -hex 64` |
| `realtime-metrics-jwt-secret` | JWT secret for Realtime's own `/metrics` endpoint | `openssl rand -hex 32` |

> [!NOTE]
> You can spin up a free Supabase **cloud project just to get the JWT values**, then self-host with those same values. Or generate them yourself — the JWT secret is just a long random string; the anon/service-role keys are JWTs signed with that secret.

**Generating Supabase JWTs manually:**
```bash
# JWT secret (generate once, keep forever)
SUPABASE_JWT_SECRET=$(openssl rand -hex 40)

# Anon key payload: role=anon, iss=supabase
# Service-role key payload: role=service_role, iss=supabase
# Use jwt.io or: python3 -c "import jwt; print(jwt.encode({'role':'anon','iss':'supabase'}, 'YOUR_SECRET', algorithm='HS256'))"
```

### 4.3 — AI / ML provider keys (from third-party APIs)

All inference credentials live in **one** SSM parameter, a JSON object:

| SSM Parameter Name | Contents |
|---|---|
| `ai-secrets-bundle` | `{"<key>": "<credential>", …}`. Each registry route names its key as `bundle:<key>`. Store it minified, on one line |

The routes themselves live in the inference registry, a JSON file shipped with the release as `infra/ai-registry/registry.json` (see [Compose VM deployment](motionly-main/Motionly-main/docs/ops/compose-vm-deployment.md#inference-registry)). The registry holds secret *references*, never values.

> [!IMPORTANT]
> **Upgrading from a release with per-vendor keys:** once the new release is healthy, delete these SSM parameters and secret files. They are no longer read: `groq-api-key`, `openrouter-api-key`, `runway-api-key`, `azure-openai-api-key`, `jina-api-key`, and the object-detection, diarization and stem-separation keys `google-vision-api-key`, `pyannoteai-api-key`, `lalal-license-key` (secret files `google_vision_api_key`, `pyannoteai_api_key`, `lalal_license_key`). Also remove these variables from env files: `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `RUNWAY_API_KEY`, `JINA_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `MODEL_GATEWAY_ROUTES`, `MODEL_GATEWAY_OPENAI_BASE_URL`, `MODEL_GATEWAY_OPENAI_API_KEY`, `TRANSCRIPTION_PROVIDER`, `EDIT_PLANNER_PROVIDER`, `VISUAL_CAPTIONS_PROVIDER`, `VISUAL_EMBEDDINGS_PROVIDER`, `EMBEDDINGS_PROVIDER`, every `QUOTA_OPENROUTER_*` / `QUOTA_RUNWAY_*` (now `QUOTA_PLANNING_*` / `QUOTA_VIDEO_GENERATION_*`), and the object-detection, diarization and stem-separation vendor variables removed in the provider-neutral release (plan Task 19 item 2): `VISUAL_OBJECTS_PROVIDER`, `GOOGLE_VISION_API_KEY`, `GOOGLE_VISION_OBJECTS_MODEL`, `GOOGLE_VISION_OBJECTS_MODEL_REVISION`, `GOOGLE_VISION_COST_PER_IMAGE_USD`, `PYANNOTEAI_API_KEY`, `PYANNOTEAI_MODEL`, `PYANNOTEAI_COST_PER_HOUR_USD`, `PYANNOTEAI_POLL_INTERVAL_SECONDS`, `PYANNOTEAI_TIMEOUT_SECONDS`, `LALAL_LICENSE_KEY`, `LALAL_SPLITTER`, `LALAL_COST_PER_MINUTE_USD`, `LALAL_POLL_INTERVAL_SECONDS`, `LALAL_TIMEOUT_SECONDS`. `AUDIO_DIARIZATION_PROVIDER` now accepts only `local_ml`/`disabled` (its `pyannoteai` value is gone) and `STEM_SEPARATION_PROVIDER` only `demucs`/`fixture` (its `lalal` value is gone); object detection, diarization and stem separation are configured through registry routes whose credentials live in `ai-secrets-bundle`.

### 4.4 — Stock media keys

| SSM Parameter Name | Where to get it |
|---|---|
| `pexels-api-key` (set in env, not SSM) | pexels.com/api → My Keys |
| `pixabay-api-key` (set in env, not SSM) | pixabay.com/api → Get API Key |

> [!NOTE]
> Pexels and Pixabay keys go in the `.env` file / Docker environment, not as Docker secrets, because they are low-risk read-only API keys.

### 4.5 — Temporal TLS certificates (if self-hosted Temporal)

The self-hosted Temporal stack requires mTLS. You need to generate a CA and four certificate/key pairs:

| SSM Parameter Name | Description |
|---|---|
| `temporal-ca-cert` | Root CA certificate (PEM, base64-encoded for SSM) |
| `temporal-server-cert` | Temporal server TLS cert |
| `temporal-server-key` | Temporal server TLS key |
| `temporal-internode-cert` | Inter-node Temporal cert |
| `temporal-internode-key` | Inter-node Temporal key |
| `temporal-diagnostics-cert` | Diagnostics endpoint cert |
| `temporal-diagnostics-key` | Diagnostics endpoint key |
| `temporal-api-cert` | API service client cert (for the `api` container) |
| `temporal-api-key` | API service client key |

```bash
# Generate a self-signed CA + server cert (example — use your own PKI tool)
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt -subj "/CN=temporal-ca"
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=temporal"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 3650
# Then base64-encode for SSM: base64 -w0 ca.crt
```

> [!TIP]
> **If you use Temporal Cloud instead of self-hosted:** You skip all of these certs. Set `TEMPORAL_ADDRESS=your-namespace.tmprl.cloud:7233`, `TEMPORAL_NAMESPACE=your-namespace.your-account`, `TEMPORAL_API_KEY=<cloud api key>`, and `TEMPORAL_TLS_ENABLED=true`. Temporal Cloud provides its own TLS.

### 4.6 — Container registry token

| SSM Parameter Name | Description |
|---|---|
| `registry-read-token` | A GitHub Classic PAT or fine-grained token with `read:packages` scope, used by the instance to pull images from GHCR |

---

## 5. Phase 1 — One-Time AWS Infrastructure Setup

### 5.1 Prerequisites on your local machine

```bash
# AWS CLI v2 + credentials for an admin-level IAM identity
aws configure
# Python 3.12+ (for the scripts)
python3 --version
# cfn-lint (optional but recommended)
pip install cfn-lint
```

### 5.2 Render the CloudFormation template

The template is generated from the edge contract (so CloudFront and the CPU-stage simulator stay in sync):

```bash
cd motionly-main/Motionly-main
python scripts/aws/render_template.py
# Outputs: infra/aws/motionly.template.json
```

### 5.3 Deploy the CloudFormation stack

```bash
# Find the CloudFront origin-facing prefix list ID in ap-south-1
aws ec2 describe-managed-prefix-lists \
  --region ap-south-1 \
  --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
  --query 'PrefixLists[0].PrefixListId' \
  --output text
# e.g. pl-0123456789abcdef

# Deploy the stack (GPU production profile)
aws cloudformation deploy \
  --region ap-south-1 \
  --template-file infra/aws/motionly.template.json \
  --stack-name motionly-production \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    Environment=production \
    ComputeProfile=gpu \
    CloudFrontOriginPrefixListId=pl-XXXXXXXXXXXXXXXX \
    OriginVerifySecret=YOUR_RANDOM_32CHAR_SECRET \
    GitHubRepository=your-org/your-repo
```

**After deploy, capture the outputs:**
```bash
aws cloudformation describe-stacks \
  --region ap-south-1 \
  --stack-name motionly-production \
  --query 'Stacks[0].Outputs'
# Note: PublicOrigin, InstanceId, GitHubDeployRoleArn, ReleasesBucketName
```

**What gets created:**
- VPC + public subnet + internet gateway
- EC2 `g6.12xlarge` instance (no SSH, no key pair — access via SSM Session Manager)
- Elastic IP bound to the instance
- Encrypted gp3 EBS data volume (mounted at `/srv/motionly`)
- CloudFront distribution pointing at the EC2 instance's IP
- S3 buckets: `releases`, `media`, `backups`
- IAM roles for the instance and for GitHub OIDC

### 5.4 Populate SSM Parameter Store

```bash
ENVIRONMENT=production
REGION=ap-south-1
PREFIX=/motionly/$ENVIRONMENT

# All secrets — run for each one:
aws ssm put-parameter \
  --region $REGION \
  --name "$PREFIX/postgres-password" \
  --value "$(openssl rand -hex 32)" \
  --type SecureString

# Repeat for every secret in Section 4 above.
# For certificates, base64-encode first:
aws ssm put-parameter \
  --region $REGION \
  --name "$PREFIX/temporal-ca-cert" \
  --value "$(base64 -w0 ca.crt)" \
  --type SecureString
```

---

## 6. Phase 2 — Supabase Setup

Since Supabase runs **self-hosted inside Docker** (using the official `supabase/postgres:17.4.1` image), you primarily need:

1. **Your JWT secret and derived keys** (stored in SSM, see §4.2)
2. **The Supabase worker JWT** — a special restricted JWT whose `role` claim is `motionly_worker`:

```python
import jwt, time

payload = {
    "role": "motionly_worker",
    "iss": "supabase",
    "iat": int(time.time()),
    "exp": int(time.time()) + (365 * 24 * 3600),  # 1 year
}
worker_key = jwt.encode(payload, SUPABASE_JWT_SECRET, algorithm="HS256")
print(worker_key)
```

3. **Storage buckets** — The migrations create the DB schema. The Supabase Storage buckets (`raw-uploads` and `exports`) are created automatically by the migrations.

> [!IMPORTANT]
> The Supabase instance is entirely self-hosted. You do **not** need a Supabase cloud account in production. The Docker image brings GoTrue (auth), PostgREST (API), Storage, and Realtime.

---

## 7. Phase 3 — GitHub Secrets & Variables

In your GitHub repository, create a **`production` Environment** with required reviewers (this is what gates the deploy approval).

### Repository Variables (non-secret, in the `production` environment)

| Variable | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | From CloudFormation output `GitHubDeployRoleArn` |
| `AWS_RELEASE_BUCKET` | From CloudFormation output `ReleasesBucketName` |
| `AWS_INSTANCE_ID` | From CloudFormation output `InstanceId` |

### GitHub Secrets (for CI builds)

The supply-chain workflow needs to push images to GHCR. This uses the built-in `GITHUB_TOKEN` — **no extra secret needed** as long as the repo has `packages: write` permission in the workflow.

---

## 8. Phase 4 — Bootstrap the EC2 Host

Since there's no SSH, connect via **SSM Session Manager**:

```bash
aws ssm start-session \
  --region ap-south-1 \
  --target i-XXXXXXXXXXXXXXXXX
```

Once on the instance:

```bash
# Clone the release tag you want to deploy
git clone https://github.com/your-org/your-repo.git /tmp/motionly-bootstrap
cd /tmp/motionly-bootstrap/motionly-main/Motionly-main

# Run the one-time host bootstrap script
sudo scripts/deploy/bootstrap_compose_host.sh
```

Then fill in `/etc/motionly/host.env` (non-secret values only, `root:root`, mode `0600`):

```bash
sudo tee /etc/motionly/host.env <<EOF
MOTIONLY_ENVIRONMENT=production
MOTIONLY_RELEASE_BUCKET=your-releases-bucket-name
MOTIONLY_COMPUTE_PROFILE=gpu
MOTIONLY_REGISTRY_USERNAME=your-github-username
MOTIONLY_APP_DOMAIN=your-cloudfront-domain.cloudfront.net
EOF
sudo chmod 600 /etc/motionly/host.env
```

---

## 9. Phase 5 — First Release & Deploy

### 9.1 Tag a release

```bash
git tag v1.0.0
git push origin v1.0.0
```

This triggers `.github/workflows/supply-chain.yml`, which:
1. Builds all Docker images for `linux/amd64`
2. Pushes them to `ghcr.io/your-org/...` with digest pins
3. Signs each image with **Cosign**
4. Creates a signed release manifest
5. Triggers `.github/workflows/deploy-aws.yml`

### 9.2 The deploy workflow (automatic after supply-chain)

1. Uploads release bundle + signed manifest to the S3 releases bucket
2. Runs **preflight** on the instance via SSM (validates images without touching running containers)
3. **Waits for your approval** in the `production` GitHub Environment
4. After approval: the agent on the instance:
   - Reads all SSM parameters
   - Pulls images by digest
   - Runs `docker compose up --profile gpu -d --wait`
   - Runs `db-migrate` (applies every migration under `supabase/migrations/` under an advisory lock)
   - Waits for all health checks to pass
   - **Rolls back automatically** if any service fails to become healthy

### 9.3 After first deploy — set up your admin user

```bash
# On the instance via SSM Session Manager, inside the api container:
docker exec -it motionly-api-1 \
  bash -c "APP_ENV=production python -m app.ops.find_user admin@yourdomain.com"
# Copy the UUID printed and add it to the api service environment:
# ADMIN_USER_IDS=<uuid>
# Then restart the api service.
```

---

## 10. Ongoing Release Flow

Every subsequent deploy is the same: push a `v*` tag → supply chain runs → deploy workflow runs → approve in GitHub → done. The agent handles rollback automatically.

**Manual rollback:**
```bash
# Via SSM Session Manager on the instance
sudo /opt/motionly/agent/bin/apply-release --rollback
```

### 10.1 Upgrading to the provider-neutral inference release

This release moves every AI provider into the inference registry and adds forward-only migrations: `202610240101`–`202610240103`, `202610240201`, `202610240202` and `202610240301`. Migration `202610240202` rewrites generated B-roll rows to neutral fields once. Before tagging:

1. Write and validate `infra/ai-registry/registry.json` and store its credentials in `ai-secrets-bundle` (Section 4.3).
2. Take a database backup ([Backups and restore](motionly-main/Motionly-main/docs/backups-restore.md)). Rollback means restoring that backup and redeploying the previous release; the automatic rollback cannot undo the data rewrite.
3. After the release is healthy, delete the old per-vendor parameters and variables listed in Section 4.3.

---

## 11. Environment Variables Reference

All of these come from [`.env.example`](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/.env.example). They are written to a protected per-release env file by the deploy agent (reading from SSM). You should not need to set these manually — the agent does it.

### Core / Required

| Variable | Description | Example |
|---|---|---|
| `APP_ENV` | Runtime environment | `production` |
| `PUBLIC_ORIGIN` | The one public HTTPS origin | `https://your.cloudfront.net` |
| `API_CORS_ORIGINS` | Allowed CORS origin(s) | same as `PUBLIC_ORIGIN` |
| `API_PUBLIC_BASE_URL` | Public API base URL | `https://your.cloudfront.net/api` |
| `SUPABASE_URL` | Internal Supabase gateway URL | `http://supabase-gateway:8000` |
| `SUPABASE_PUBLIC_URL` | Public-facing Supabase URL | `https://your.cloudfront.net/supabase` |
| `SUPABASE_ANON_KEY` | Supabase anon JWT | (generated) |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role JWT | (generated) |
| `SUPABASE_JWT_SECRET` | Supabase JWT signing secret | (generated) |
| `REDIS_URL` | Redis connection string | `redis://:password@redis:6379/0` |

### AI / ML Providers

| Variable | Description |
|---|---|
| `AI_REGISTRY_FILE` | Inference registry path, or a comma-separated list of fragments. Required in production; production Compose sets `/opt/motionly/ai-registry/registry.json` |
| `AI_SECRETS_BUNDLE_FILE` | JSON object of route credentials for `bundle:` refs. Production Compose mounts the `ai_secrets_bundle` secret |
| `AI_SECRETS_DIR` | Directory for `file:` refs (default `/run/secrets`) |
| `AI_QUOTA_SCOPE` | `feature` (default) or `provider` (temporary stop-gap) |
| `AI_BLOCK_NONCOMMERCIAL` | `true` makes non-commercial local model licences an error |
| `PEXELS_API_KEY` | Pexels stock video |
| `PIXABAY_API_KEY` | Pixabay stock video |

### Temporal Workflow

| Variable | Description |
|---|---|
| `TEMPORAL_ADDRESS` | Temporal server address (`temporal:7233` self-hosted, or `namespace.tmprl.cloud:7233` for cloud) |
| `TEMPORAL_NAMESPACE` | Temporal namespace |
| `TEMPORAL_API_KEY` | Temporal Cloud API key (cloud only) |
| `TEMPORAL_TLS_ENABLED` | `true` in production |
| `TEMPORAL_TASK_QUEUE` | `motionly-workflows` |

### Model Revisions (pinned — do not change without testing)

Model ids, pinned revisions, licences and weight hashes are no longer environment variables. They are registry records: local CPU models in `infra/ai-registry/cpu-local.json` (MediaPipe matte, YAMNet, Demucs) and local GPU models in `infra/ai-registry/gpu-local.json` (CLIP, BLIP, YOLO, pyannote, wav2vec2). The old `*_MODEL` / `*_MODEL_REVISION` variables are ignored and can be removed.

### Security / Admin

| Variable | Description |
|---|---|
| `ADMIN_API_TOKEN` | Bearer token for admin endpoints |
| `WORKER_API_TOKEN` | Bearer token workers use to call API |
| `ADMIN_USER_IDS` | Comma-separated UUIDs of admin users |
| `METRICS_BEARER_TOKEN` | Bearer token for Prometheus `/metrics` scrape |
| `RATE_LIMIT_PER_MINUTE` | Per-viewer API rate limit (default: `120`) |

### Storage / Rendering

| Variable | Default |
|---|---|
| `UPLOAD_STORAGE_BUCKET` | `raw-uploads` |
| `EXPORT_STORAGE_BUCKET` | `exports` |
| `UPLOAD_MAX_FILE_SIZE_BYTES` | `536870912` (512 MB) |
| `RETENTION_SOURCE_DAYS` | `365` |
| `RETENTION_EXPORT_DAYS` | `30` |
| `RENDER_EXECUTE_FFMPEG` | `true` |
| `REMOTION_ENABLED` | `false` (set `true` to enable B-roll/effects rendering) |

### Billing (Demo Gateway — no real payments)

| Variable | Description |
|---|---|
| `BILLING_ENABLED` | `true` to show billing UI |
| `BILLING_MODE` | `disabled` or `demo` (production requires one of these) |
| `MOCK_GATEWAY_BASE_URL` | `http://payment-gateway:8010` |
| `MERCHANT_API_KEY` | Shared key between API and gateway |
| `MOCK_GATEWAY_WEBHOOK_SECRET` | HMAC secret for webhook verification |

---

## 12. Minimal / Budget Setup (No GPU)

If you want to run it cheaply without GPU (no background removal, no visual AI analysis):

| Concern | Budget choice |
|---|---|
| **Instance** | `m7i.2xlarge` (`ComputeProfile=cpu` in CloudFormation) |
| **GPU workers** | Skip — set `AUDIO_ML_PROVIDER=disabled`, `VISUAL_PROVIDER=disabled` |
| **Temporal** | Use **Temporal Cloud** free tier instead of self-hosting |
| **Transcription** | Any low-cost OpenAI-compatible transcription route |
| **Planning** | Any low-cost OpenAI-compatible chat route |
| **Video generation** | Configure no `video.generation.submit` route (the feature reports unavailable) |
| **Docker Compose profile** | Use `--profile slim` instead of `--profile gpu` |

**Estimated cost: ~$200–400/month** (m7i.2xlarge ~$300/mo + planning and transcription API usage)

**GPU production cost: ~$1,500–2,500/month** (g6.12xlarge ~$2,000/mo in Mumbai + AI API usage)

> [!NOTE]
> To keep visual analysis, background removal and audio intelligence without a GPU, use [Section 13](#13-gpu-free-remote-ai-mode) instead: GPU work goes to remote APIs and CPU work stays local.

---

## 13. GPU-free Remote AI Mode

Every feature that needs a GPU calls a remote API; everything that runs on a CPU stays local. Each feature has its own switch, and the defaults (no switch set) are the GPU topology above, unchanged.

### 13.1 Feature switches

Remote AI features are registry routes ([registry guide](motionly-main/Motionly-main/docs/ai-registry/README.md)). A capability without a route reports `unavailable` at `GET /capabilities`, and editing continues with its fallback. The switches below place the work that stays local.

| Feature | GPU-free setup | Runs on | Fallback without a route |
|---|---|---|---|
| Visual analysis (frames, scenes, faces, colours, motion) | `VISUAL_PROVIDER=remote` | `cpu-vision-worker` (OpenCV + MediaPipe) | — |
| Keyframe objects | a `vision.object_detection` route (example: `docs/ai-registry/examples/object-detection.json`) | the configured remote API | empty detections, `objects: unavailable:not_configured` |
| Keyframe captions, voice emotion, embeddings | not available until Tier 2 (registry routes, plan Tasks 11–12) | — | captions and emotion degraded; B-roll ranks by keywords |
| Background matte | `MATTE_PROVIDER=local_cpu`, or the matte route in `infra/ai-registry/cpu-local.json` | `cpu-vision-worker` (MediaPipe selfie segmenter) | matte effects disabled |
| Speaker diarization | an `audio.diarization` route (example: `docs/ai-registry/examples/diarization.json`) | the configured remote API | heuristic speaker labels, marked degraded |
| Sound classification | `AUDIO_SOUND_PROVIDER=yamnet_tflite` | `cpu-vision-worker` (YAMNet TFLite) | labels empty |
| Stem separation | an `audio.stem_separation` route: CPU Demucs from `cpu-local.json`, or a remote API (example: `docs/ai-registry/examples/stem-separation.json`) | `demucs-worker` (profile `local-stems`) or the remote API | stem request refused with `503 capability_unavailable` |
| Rendering | `FFMPEG_GPU_ENABLED=false` (libx264 on `cpu-ffmpeg-worker`) | — | — |
| GPU workers present | `GPU_WORKERS_ENABLED=false` | — | — |

Only scene keyframes (at most `VISUAL_REMOTE_MAX_KEYFRAMES`, default 24 per video) go to remote vision APIs; the video never leaves the deployment. With `GPU_WORKERS_ENABLED=false` the GPU capacity observer, its maintenance job and GPU model prefetch are off, and routes with `requires_gpu: true` are dropped at startup.

### 13.2 Accounts, keys and prices

For each remote feature, create an account with any provider whose API fits the example route, copy the example into your registry, and set:

- `base_url` and the auth header the provider expects;
- `secret_ref: "bundle:<key>"`, with the credential stored under that key in `ai-secrets-bundle`;
- `pricing` in the route's billing unit (per image, per second of audio, …), or `explicitly_free: true`, or `pricing.status: unknown` with a `budget.hard_cap`;
- `data_processor` metadata (display name, purpose, data categories, policy URL). The privacy page lists exactly these processors, and production refuses a remote route without them.

Validate before deploying: `cd apps/api && python -m app.ops.validate_inference_registry <registry.json> --app-env production`.

### 13.3 Bring-up

1. **Image.** The supply-chain workflow builds the `cpu-vision-worker` component. To build it yourself:
   `docker build -f apps/worker/Dockerfile.python --build-arg API_EXTRAS=cpu-vision --build-arg CPU_VISION_ENABLED=true -t <registry>/cpu-vision-worker .`
   It bakes in the YAMNet, selfie-segmenter and BlazeFace models, each checked against a pinned SHA-256. Set `MOTIONLY_CPU_VISION_WORKER_IMAGE=<name>@sha256:<digest>`.
2. **Database.** Migration `202610220101_remote_ai_feature_quota_categories.sql` must be applied (`db-migrate` runs it on start). Without it, quota usage rows are rejected.
3. **Inputs.** Add the image and any switch overrides to the Compose env file (template: `infra/production-compose.env.example`), the registry (with any remote routes) to `infra/ai-registry/registry.json`, and the route credentials to the secrets bundle.
4. **Start**, without `--profile gpu`:
   ```bash
   docker compose --project-name motionly --env-file compose.env \
     -f infra/docker-compose.gpu.yml -f infra/docker-compose.remote-ai.yml up -d --wait
   ```
   No GPU service and no `demucs-worker` start. `cpu-vision-worker` serves `visual_analysis,transcription,matte_extraction,audio_demucs`.
5. **Check.** `GET /api/capabilities` lists every capability as `available`, `degraded` or `unavailable` with a reason. `GET /api/health/dependencies` (authenticated) has an `inference` entry with validation warnings, dropped routes and circuit state; it never contains key values. Worker heartbeats from `cpu-vision-worker` list `cpu_vision_queues: [visual_analysis, matte_extraction]`.

> [!IMPORTANT]
> The signed release flow (`scripts/deploy/apply_release.sh`) deploys only `infra/docker-compose.gpu.yml` and refuses `MOTIONLY_COMPOSE_OVERLAY` outside rehearsal. The release manifest does not carry the cpu-vision image. Until it does, run this mode with operator-run Compose as above. The agent does fetch `ai-secrets-bundle`, so registry routes need no extra SSM parameters.

> [!NOTE]
> **Signed-release GPU-free deployment (recommended).** The release agent now deploys this overlay for you when the host opts in: set `MOTIONLY_COMPUTE_PROFILE=cpu`, `MOTIONLY_CPU_TOPOLOGY=gpu-free`, and `MOTIONLY_LOCAL_STEMS=enabled` in `/etc/motionly/host.env`. The signed manifest now carries the optional `MOTIONLY_CPU_VISION_WORKER_IMAGE` (built by the supply-chain `cpu-vision-worker` component) and the release bundle carries `infra/docker-compose.remote-ai.yml`, so the agent adds the overlay and the `local-stems` profile on top of `docker-compose.gpu.yml` — no operator-run Compose needed. The agent still refuses `MOTIONLY_COMPOSE_OVERLAY` outside rehearsal; `gpu-free` selects a bundled overlay, not an operator override. It records the topology per release, so rollback reuses it. Both settings default to the GPU topology, so existing hosts and already-published releases are unchanged. The operator-run Compose command above remains a supported fallback. See [`docs/aws/first-paid-test.md`](motionly-main/Motionly-main/docs/aws/first-paid-test.md) and [Compose VM deployment](motionly-main/Motionly-main/docs/ops/compose-vm-deployment.md).

### 13.4 Cost controls and failure behaviour

| Control | Setting | Scope |
|---|---|---|
| Monthly budget | `MODEL_GATEWAY_MONTHLY_BUDGET_USD` | per workspace, all features |
| Request budget | `MODEL_GATEWAY_REQUEST_BUDGET_USD` | per call |
| Monthly cap per feature | `MODEL_GATEWAY_FEATURE_BUDGETS_USD`, e.g. `{"vision_captions":20,"audio_stems":15}` | whole deployment |
| Plan quota per feature | `QUOTA_<FEATURE>_<PLAN>_LIMIT_<UNIT>` | per workspace, per billing period |

Plan quota defaults (free / creator / pro / agency):

| Feature | Unit | Defaults |
|---|---|---|
| `VISION_OBJECTS`, `VISION_CAPTIONS` | images | 120 / 1,000 / 3,000 / 7,500 |
| `EMBEDDINGS` | inputs | 1,000 / 8,000 / 24,000 / 60,000 |
| `AUDIO_DIARIZATION`, `AUDIO_EMOTION` | seconds | 1,800 / 14,400 / 43,200 / 108,000 |
| `AUDIO_STEMS` | seconds | 600 / 3,600 / 10,800 / 36,000 |

Example: `QUOTA_VISION_CAPTIONS_FREE_LIMIT_IMAGES=60`. Only remote calls count. Usage is recorded once per successful call in `cost_attribution_events`. If the quota cannot be checked, the paid call is refused.

When a provider fails, its circuit is open, or a budget or quota runs out:

- **Visual analysis** completes and lists the missing part in `summary.degraded_capabilities` (for example `objects: unavailable:quota_exceeded`). The other capabilities still run.
- **Diarization, voice emotion, sound** keep the transcript-based estimate and record `ml_status`.
- **B-roll** ranks a slot by keywords when its query embedding is refused, and does not cache that slot set.
- **Stems** fail visibly so the user can retry: HTTP 402 `audio_stems_quota_exceeded` when the quota is used up, and a failed job for provider errors or budgets.

### 13.5 Switching a feature back to the GPU

General rules:

- Set `GPU_WORKERS_ENABLED=true` as soon as any GPU service runs; production refuses local GPU switches while it is `false`.
- Set switches in the Compose env file; the overlay passes them to the API and every worker, so they always agree.
- Remove a queue from `CPU_VISION_WORKER_QUEUES` when a GPU worker takes it over. A cpu-vision worker subscribed to `visual_analysis` or `matte_extraction` refuses to start if the matching switch is a GPU value.
- `--profile gpu` still starts every GPU service at once.

| Feature | Set | Start | Notes |
|---|---|---|---|
| Visual analysis | `VISUAL_PROVIDER=local_ml`, `GPU_WORKERS_ENABLED=true`, add `infra/ai-registry/gpu-local.json` to `AI_REGISTRY_FILE`, `CPU_VISION_WORKER_QUEUES=transcription,matte_extraction,audio_demucs` | `--profile gpu-inference` (serves `GPU_INFERENCE_WORKER_QUEUES`, default `visual_analysis`) | Text and image embedding routes used together must share one `embedding_space`; the validator refuses a mix. The visual cache identity changes, so new analyses are not served from remote-mode cache entries. |
| Matte | `MATTE_PROVIDER=local_ml`, drop `matte_extraction` from `CPU_VISION_WORKER_QUEUES` | `--profile gpu-matte` | |
| Diarization / voice emotion / sound | `AUDIO_DIARIZATION_PROVIDER`, `AUDIO_EMOTION_PROVIDER`, `AUDIO_SOUND_PROVIDER` = `local_ml` (or empty); drop `transcription` from `CPU_VISION_WORKER_QUEUES`; `GPU_INFERENCE_WORKER_QUEUES=visual_analysis,transcription` | `--profile gpu-inference` | The local models run inside transcription jobs, so the transcription queue moves with them. Diarization also needs `PYANNOTE_AUTH_TOKEN`. |
| Stems | the Demucs route from `infra/ai-registry/cpu-local.json` (`STEM_SEPARATION_PROVIDER=demucs`), drop `audio_demucs` from `CPU_VISION_WORKER_QUEUES` | `--profile local-stems` (Demucs runs on CPU) | |
| B-roll embeddings only | the CLIP route from `infra/ai-registry/gpu-local.json` | — | CLIP runs inside the process that ranks B-roll (`planning-worker`), which needs `torch`; see the known issues. |
| Rendering | `FFMPEG_GPU_ENABLED=true` on the API | `--profile gpu-render` | |

To go fully back to the original topology, drop the overlay file and start with `--profile gpu`.

### 13.6 Known issues

- **MediaPipe legacy API (pre-existing).** The locked `mediapipe==0.10.35` no longer ships `mp.solutions`. The GPU visual provider (`LocalMLVisualIntelligenceProvider._load_models`) and the GPU matte loader still call `mp.solutions.face_detection` / `selfie_segmentation`, so they would fail to load on the GPU image too. The CPU path uses the MediaPipe Tasks API and is not affected. Fix the GPU path before switching visual analysis or matte back.
- **Local CLIP on the planning worker (pre-existing, from the lock files).** `torch` and `transformers` are only in `requirements-ml.lock`, while `planning-worker` (which runs B-roll generation) uses the `cpu-worker` image. With a local CLIP route, B-roll embeddings would fail there; ranking then falls back to keywords. Not tested at runtime.
- **Not verified against live providers.** The registry template routes were tested against recorded API shapes and differential tests against the previous adapters, not live accounts. One live run per remote capability is mandatory before the previous adapters are removed ([live-run log](motionly-main/Motionly-main/docs/ai-registry/live-runs.md)).
- **Privacy page.** `apps/web/app/privacy/page.tsx` renders the `data_processor` metadata of the enabled remote routes. Confirm each processor's retention terms before publishing.

---

## Summary Checklist

```
[ ] AWS account with ap-south-1 access
[ ] GitHub repo with GHCR access
[ ] Planning and transcription endpoints chosen; registry written to infra/ai-registry/registry.json and validated
[ ] ai-secrets-bundle SSM parameter holds every bundle: key the registry references
[ ] Video-generation route (optional)
[ ] Pexels + Pixabay API keys (for stock video)
[ ] Generate all random secrets (postgres, redis, admin tokens, etc.)
[ ] Deploy CloudFormation stack
[ ] Populate all SSM parameters
[ ] Bootstrap EC2 host via SSM Session Manager
[ ] Fill /etc/motionly/host.env
[ ] Push a v* tag → supply chain runs → approve deploy
[ ] Set ADMIN_USER_IDS after first login
[ ] Set up Prometheus scraper for /metrics (optional but recommended)
```
