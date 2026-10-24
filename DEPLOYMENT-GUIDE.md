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
| **Groq** | Audio transcription (Whisper large-v3-turbo) | console.groq.com |
| **OpenRouter** | AI edit planning (GPT-4.1-mini and others) | openrouter.ai |
| **GitHub** | Container registry (GHCR) + CI/CD | github.com |

### Required for premium rendering (B-roll, visual effects)

| Service | Purpose | Where to sign up |
|---|---|---|
| **Runway** | AI video generation (gen3a_turbo) | runwayml.com |
| **Pexels** | Stock video search | pexels.com/api |
| **Pixabay** | Stock video search (fallback) | pixabay.com/api |

### Optional / advanced

| Service | Purpose |
|---|---|
| **Azure OpenAI** | Alternative LLM provider for planning |
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

| SSM Parameter Name | Where to get it |
|---|---|
| `groq-api-key` | console.groq.com → API Keys |
| `openrouter-api-key` | openrouter.ai → Keys |
| `runway-api-key` | app.runwayml.com → API |
| `azure-openai-api-key` | Azure Portal → Azure OpenAI resource |

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
   - Runs `db-migrate` (applies all 108 migrations under advisory lock)
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
| `GROQ_API_KEY` | Groq API key (transcription) |
| `OPENROUTER_API_KEY` | OpenRouter API key (planning) |
| `RUNWAY_API_KEY` | Runway API key (video generation) |
| `PEXELS_API_KEY` | Pexels stock video |
| `PIXABAY_API_KEY` | Pixabay stock video |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint (optional) |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI key (optional) |

### Temporal Workflow

| Variable | Description |
|---|---|
| `TEMPORAL_ADDRESS` | Temporal server address (`temporal:7233` self-hosted, or `namespace.tmprl.cloud:7233` for cloud) |
| `TEMPORAL_NAMESPACE` | Temporal namespace |
| `TEMPORAL_API_KEY` | Temporal Cloud API key (cloud only) |
| `TEMPORAL_TLS_ENABLED` | `true` in production |
| `TEMPORAL_TASK_QUEUE` | `motionly-workflows` |

### Model Revisions (pinned — do not change without testing)

| Variable | Value |
|---|---|
| `PYANNOTE_MODEL_REVISION` | `84fd25912480287da0247647c3d2b4853cb3ee5d` |
| `WAV2VEC_EMOTION_MODEL_REVISION` | `441a7599c3b22107314dcbd9166621c5c83f2cc5` |
| `VISUAL_CLIP_MODEL_REVISION` | `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268` |
| `VISUAL_BLIP_MODEL_REVISION` | `59a1ef6c1e5117b3f65523d1c6066825bcf315e3` |
| `DEMUCS_MODEL_REVISION` | `demucs==4.1.0` |

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
| **Transcription** | Groq (very cheap — ~$0.25/request) |
| **Planning** | OpenRouter GPT-4.1-mini (cheap) |
| **Video generation** | Skip Runway (set `REMOTION_ENABLED=false`) |
| **Docker Compose profile** | Use `--profile slim` instead of `--profile gpu` |

**Estimated cost: ~$200–400/month** (m7i.2xlarge ~$300/mo + Groq/OpenRouter usage)

**GPU production cost: ~$1,500–2,500/month** (g6.12xlarge ~$2,000/mo in Mumbai + AI API usage)

> [!NOTE]
> To keep visual analysis, background removal and audio intelligence without a GPU, use [Section 13](#13-gpu-free-remote-ai-mode) instead: GPU work goes to remote APIs and CPU work stays local.

---

## 13. GPU-free Remote AI Mode

Every feature that needs a GPU calls a remote API; everything that runs on a CPU stays local. Each feature has its own switch, and the defaults (no switch set) are the GPU topology above, unchanged.

### 13.1 Feature switches

| Feature | Switch | GPU value (default) | GPU-free value | Runs on | Key |
|---|---|---|---|---|---|
| Visual analysis (frames, scenes, faces, colours, motion) | `VISUAL_PROVIDER` | `local_ml` | `remote` | `cpu-vision-worker` (OpenCV + MediaPipe) | — |
| Keyframe objects | `VISUAL_OBJECTS_PROVIDER` | part of `local_ml` | `google_vision` (or `disabled`) | Google Cloud Vision | `google_vision_api_key` |
| Keyframe captions | `VISUAL_CAPTIONS_PROVIDER` | part of `local_ml` | `openrouter` (or `disabled`) | OpenRouter, zero-data-retention endpoints only | `openrouter_api_key` |
| Keyframe embeddings | `VISUAL_EMBEDDINGS_PROVIDER` | part of `local_ml` | `jina` (or `disabled`) | Jina AI | `jina_api_key` |
| B-roll embeddings | `EMBEDDINGS_PROVIDER` | `local_clip` | `jina` (required when keyframe embeddings use Jina) | Jina AI | `jina_api_key` |
| Background matte | `MATTE_PROVIDER` | `local_ml` | `local_cpu` | `cpu-vision-worker` (MediaPipe selfie segmenter) | — |
| Speaker diarization | `AUDIO_DIARIZATION_PROVIDER` | empty (= `local_ml`) | `pyannoteai` | pyannoteAI | `pyannoteai_api_key` |
| Voice emotion | `AUDIO_EMOTION_PROVIDER` | empty (= `local_ml`) | `openrouter_audio` | OpenRouter, zero-data-retention endpoints only | `openrouter_api_key` |
| Sound classification | `AUDIO_SOUND_PROVIDER` | empty (= `local_ml`) | `yamnet_tflite` | `cpu-vision-worker` (YAMNet TFLite) | — |
| Stem separation | `STEM_SEPARATION_PROVIDER` | `demucs` | `lalal` | LALAL.AI | `lalal_license_key` |
| Rendering | `FFMPEG_GPU_ENABLED` (API) | `true` sends renders to `gpu-ffmpeg-worker` | `false` (libx264 on `cpu-ffmpeg-worker`) | — | — |
| GPU workers present | `GPU_WORKERS_ENABLED` | `true` | `false` | — | — |

Only scene keyframes (at most `VISUAL_REMOTE_MAX_KEYFRAMES`, default 24 per video) go to the vision and embedding APIs; the video never leaves the deployment. With `GPU_WORKERS_ENABLED=false` the GPU capacity observer, its maintenance job and GPU model prefetch are off, and production start-up refuses any switch that still needs a GPU worker.

Transcription (Groq) and planning (OpenRouter) are already remote and do not change.

### 13.2 Accounts, keys and prices

Create these accounts (OpenRouter and Groq are already required):

| Vendor | Used for | Sign-up | Pricing |
|---|---|---|---|
| Google Cloud | Object localization | console.cloud.google.com → project with billing → enable **Cloud Vision API** → API key restricted to that API | cloud.google.com/vision/pricing (Object Localization) |
| Jina AI | Image and text embeddings (`jina-clip-v2`, 1024 dimensions) | jina.ai → API key | jina.ai/embeddings (Pricing) |
| OpenRouter | Captions and voice emotion (`google/gemini-3.5-flash-lite`) | existing key | the model's page on openrouter.ai |
| pyannoteAI | Diarization (`precision-3`) | dashboard.pyannote.ai → API key | pyannote.ai/pricing |
| LALAL.AI | Stems | lalal.ai/api → API license key | lalal.ai/pricing and lalal.ai/api |

Write each key to a file in `SECRETS_DIR` (mode `0444`, directory `0700`): `google_vision_api_key`, `jina_api_key`, `pyannoteai_api_key`, `lalal_license_key` ([docs/security/secrets.md](motionly-main/Motionly-main/docs/security/secrets.md)).

Every remote call reserves budget from the prices below, so they must match your contract. Production refuses zero prices, and the overlay refuses to start without them. Convert to the setting's unit:

| Setting | Unit |
|---|---|
| `GOOGLE_VISION_COST_PER_IMAGE_USD` | USD per image (Google prices per 1,000) |
| `VISUAL_CAPTION_INPUT_COST_PER_MILLION_USD`, `VISUAL_CAPTION_OUTPUT_COST_PER_MILLION_USD` | USD per million input / output tokens of the caption model |
| `JINA_TEXT_COST_PER_MILLION_TOKENS_USD` | USD per million text tokens (charged per UTF-8 byte, an upper bound) |
| `JINA_IMAGE_COST_PER_IMAGE_USD` | USD per embedded image |
| `PYANNOTEAI_COST_PER_HOUR_USD` | USD per hour of audio (pyannoteAI prices in EUR; convert) |
| `AUDIO_EMOTION_INPUT_COST_PER_MILLION_USD`, `AUDIO_EMOTION_OUTPUT_COST_PER_MILLION_USD` | USD per million audio-input / output tokens of the emotion model |
| `LALAL_COST_PER_MINUTE_USD` | USD per minute of source audio |

### 13.3 Bring-up

1. **Image.** The supply-chain workflow builds the `cpu-vision-worker` component. To build it yourself:
   `docker build -f apps/worker/Dockerfile.python --build-arg API_EXTRAS=cpu-vision --build-arg CPU_VISION_ENABLED=true -t <registry>/cpu-vision-worker .`
   It bakes in the YAMNet, selfie-segmenter and BlazeFace models, each checked against a pinned SHA-256. Set `MOTIONLY_CPU_VISION_WORKER_IMAGE=<name>@sha256:<digest>`.
2. **Database.** Migration `202610220101_remote_ai_feature_quota_categories.sql` must be applied (`db-migrate` runs it on start). Without it, quota usage rows are rejected.
3. **Inputs.** Add the image, the nine prices and any switch overrides to the Compose env file (template: `infra/production-compose.env.example`), and the four secret files to `SECRETS_DIR`.
4. **Start**, without `--profile gpu`:
   ```bash
   docker compose --project-name motionly --env-file compose.env \
     -f infra/docker-compose.gpu.yml -f infra/docker-compose.remote-ai.yml up -d --wait
   ```
   No GPU service and no `demucs-worker` start. `cpu-vision-worker` serves `visual_analysis,transcription,matte_extraction,audio_demucs`.
5. **Check.** `GET /api/health/dependencies` (authenticated) has an `ai_features` entry: every feature should show `execution` `cpu_worker` or `remote`, `configured: true`, and `gpu_workers: disabled`. The response never contains key values. Worker heartbeats from `cpu-vision-worker` list `cpu_vision_queues: [visual_analysis, matte_extraction]`.

> [!IMPORTANT]
> The signed release flow (`scripts/deploy/apply_release.sh`) deploys only `infra/docker-compose.gpu.yml` and refuses `MOTIONLY_COMPOSE_OVERLAY` outside rehearsal. The release manifest does not carry the cpu-vision image, and the agent does not copy the four new keys from SSM. Until those are added, run this mode with operator-run Compose as above.

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
| Visual analysis | `VISUAL_PROVIDER=local_ml`, `EMBEDDINGS_PROVIDER=local_clip`, `CPU_VISION_WORKER_QUEUES=transcription,matte_extraction,audio_demucs` | `--profile gpu-inference` (serves `GPU_INFERENCE_WORKER_QUEUES`, default `visual_analysis`) | Switch embeddings together: local manifests carry 512-dimension CLIP vectors, which B-roll would otherwise compare with 1024-dimension Jina vectors. The visual cache identity changes, so new analyses are not served from remote-mode cache entries. |
| Matte | `MATTE_PROVIDER=local_ml`, drop `matte_extraction` from `CPU_VISION_WORKER_QUEUES` | `--profile gpu-matte` | |
| Diarization / voice emotion / sound | `AUDIO_DIARIZATION_PROVIDER`, `AUDIO_EMOTION_PROVIDER`, `AUDIO_SOUND_PROVIDER` = `local_ml` (or empty); drop `transcription` from `CPU_VISION_WORKER_QUEUES`; `GPU_INFERENCE_WORKER_QUEUES=visual_analysis,transcription` | `--profile gpu-inference` | The local models run inside transcription jobs, so the transcription queue moves with them. Diarization also needs `PYANNOTE_AUTH_TOKEN`. |
| Stems | `STEM_SEPARATION_PROVIDER=demucs`, drop `audio_demucs` from `CPU_VISION_WORKER_QUEUES` | `--profile local-stems` (Demucs runs on CPU) | |
| B-roll embeddings only | `EMBEDDINGS_PROVIDER=local_clip`, and `VISUAL_EMBEDDINGS_PROVIDER=disabled` while visual analysis stays remote | — | CLIP runs inside the process that ranks B-roll (`planning-worker`), which needs `torch`; see the known issues. |
| Rendering | `FFMPEG_GPU_ENABLED=true` on the API | `--profile gpu-render` | |

To go fully back to the original topology, drop the overlay file and start with `--profile gpu`.

### 13.6 Known issues

- **MediaPipe legacy API (pre-existing).** The locked `mediapipe==0.10.35` no longer ships `mp.solutions`. The GPU visual provider (`LocalMLVisualIntelligenceProvider._load_models`) and the GPU matte loader still call `mp.solutions.face_detection` / `selfie_segmentation`, so they would fail to load on the GPU image too. The CPU path uses the MediaPipe Tasks API and is not affected. Fix the GPU path before switching visual analysis or matte back.
- **Local CLIP on the planning worker (pre-existing, from the lock files).** `torch` and `transformers` are only in `requirements-ml.lock`, while `planning-worker` (which runs B-roll generation) uses the `cpu-worker` image. With `EMBEDDINGS_PROVIDER=local_clip`, production has no fallback, so B-roll embeddings would fail there. Not tested at runtime.
- **Not verified against live vendors.** All provider integrations were tested against recorded API shapes, not live accounts. Run one analysis per feature after adding keys. LALAL.AI download links are upgraded to HTTPS; whether its download host serves HTTPS was not confirmed.
- **Privacy page.** `apps/web/app/privacy/page.tsx` lists the providers above, what each receives and its retention. Confirm Jina AI's and LALAL.AI's retention terms before publishing.

---

## Summary Checklist

```
[ ] AWS account with ap-south-1 access
[ ] GitHub repo with GHCR access
[ ] Groq API key (required)
[ ] OpenRouter API key (required)
[ ] Runway API key (for premium rendering)
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
