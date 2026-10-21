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
