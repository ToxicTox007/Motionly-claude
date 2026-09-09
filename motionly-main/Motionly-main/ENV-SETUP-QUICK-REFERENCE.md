# Environment Setup Quick Reference

**TL;DR:** Run the setup script, then start Docker services.

---

## Option 1: Automated Setup (Recommended)

### Windows (PowerShell)
```powershell
.\setup-local-env.ps1
```

### Linux/Mac (Bash)
```bash
chmod +x setup-local-env.sh
./setup-local-env.sh
```

**What it creates:**
- `infra/.env.local` with 23 environment variables
- `infra/secrets/*` with 31 secret files

**Time:** ~10 seconds

---

## Option 2: Manual Setup

### Step 1: Environment Variables (5 minutes)

Copy and edit:
```bash
cd infra
cp development-compose.env.example .env.local
```

Edit `.env.local` and set these **10 required values**:

| Variable | Example Value | Notes |
|----------|--------------|-------|
| `POSTGRES_PASSWORD` | `devpassword123` | Any password for local dev |
| `REDIS_PASSWORD` | `devpassword123` | Any password for local dev |
| `TEMPORAL_DB_PASSWORD` | `devpassword123` | Any password for local dev |
| `SUPABASE_JWT_SECRET` | `super-secret-jwt-key-at-least-32-characters-long-for-dev` | At least 32 chars |
| `SUPABASE_ANON_KEY` | `anon-key-base64-encoded-at-least-32-chars-dev` | At least 32 chars |
| `SUPABASE_SERVICE_ROLE_KEY` | `service-role-key-base64-encoded-at-least-48-chars-dev` | At least 48 chars |
| `WORKER_API_TOKEN` | `dev-worker-token-123` | Any token string |
| `ADMIN_API_TOKEN` | `dev-admin-token-456` | Any token string |
| `PUBLISH_TOKEN_ENCRYPTION_KEY` | `dev-publish-encryption-key-32-chars-minimum-here` | At least 32 chars |
| `TEMPORAL_BOOTSTRAP_ADMIN_UNTIL` | `2099-12-31T23:59:59Z` | Far future date |

**Quick generate secrets:**
```bash
openssl rand -base64 32  # For JWT secret
openssl rand -base64 32  # For anon key
openssl rand -base64 48  # For service role key
```

### Step 2: Secret Files (5 minutes)

Create 31 files in `infra/secrets/`:

**Core secrets (12 files):**
```bash
cd infra/secrets

# Passwords
echo "devpassword123" > postgres_password
echo "devpassword123" > redis_password
echo "devpassword123" > temporal_db_password

# Supabase
echo "super-secret-jwt-key-at-least-32-characters-long-for-dev" > supabase_jwt_secret
echo "service-role-key-base64-encoded-at-least-48-chars-dev" > supabase_service_role_key

# Tokens
echo "dev-worker-token-123" > worker_api_token
echo "dev-admin-token-456" > admin_api_token
echo "dev-publish-encryption-key-32-chars-minimum-here" > publish_token_encryption_key
echo "dev-oauth-state-secret" > publish_oauth_state_secret
echo "dev-metrics-token" > metrics_bearer_token

# Empty API keys (for fixture mode)
touch groq_api_key openrouter_api_key runway_api_key azure_openai_api_key
```

**Temporal certs (17 files - just create empty):**
```bash
touch temporal_ca_cert temporal_server_cert temporal_server_key
touch temporal_internode_cert temporal_internode_key
touch temporal_api_cert temporal_api_key
touch temporal_dispatcher_cert temporal_dispatcher_key
touch temporal_worker_cert temporal_worker_key
touch temporal_reconciler_cert temporal_reconciler_key
touch temporal_namespace_bootstrap_cert temporal_namespace_bootstrap_key
touch temporal_diagnostics_cert temporal_diagnostics_key
```

---

## Starting Services

### Minimal Stack (Recommended for Testing)

**Just databases:**
```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d postgres redis temporal-postgres
```

**Wait 10 seconds, then verify:**
```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  ps
```

**Expected output:**
```
NAME                STATUS              PORTS
postgres            Up (healthy)        0.0.0.0:5432->5432/tcp
redis               Up (healthy)        0.0.0.0:6379->6379/tcp
temporal-postgres   Up (healthy)        5432/tcp
```

### Full Stack (All Services)

```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d --build
```

**Note:** First build will take 10-20 minutes

---

## Validation

### Verify Task 4: Data Foundations

```bash
cd apps/api
python -m pytest tests/test_database_schema_validation.py -v
```

**Expected:** 7/8 tests passing

### Verify Task 5: Temporal

```bash
cd apps/api
python -m pytest tests/test_temporal_queue_canonical.py -v
```

**Expected:** 9/10 tests passing (already verified without Docker)

### Verify Task 6: Web

```bash
cd apps/web
node --test tests/api-url-config-precedence.test.mjs
```

**Expected:** 14/14 tests passing (already verified)

---

## Troubleshooting

### Services won't start

**Check Docker is running:**
```bash
docker ps
```

**Check .env.local exists:**
```bash
ls infra/.env.local
```

**Check secrets exist:**
```bash
ls infra/secrets/postgres_password
```

### Postgres fails to start

**Error:** `postgres_password: no such file or directory`

**Fix:**
```bash
echo "devpassword123" > infra/secrets/postgres_password
```

### Temporal fails to start

**Error:** TLS certificate issues

**Fix:** Create empty cert files:
```bash
cd infra/secrets
touch temporal_ca_cert temporal_server_cert temporal_server_key
```

### "required variable X is missing"

**Fix:** Add to `infra/.env.local`:
```bash
echo "X=some-value" >> infra/.env.local
```

---

## Summary

### Required Files
- **1** `.env.local` file (23 variables)
- **31** secret files (12 core + 19 Temporal)

### Time Investment
- **Automated:** 10 seconds
- **Manual:** 10 minutes

### Commands After Setup
```bash
# Start minimal services
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d postgres redis temporal-postgres

# Verify
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml ps

# Run tests
cd apps/api
python -m pytest tests/test_database_schema_validation.py -v
```

---

## Full Documentation

See **DOCKER-COMPOSE-ENV-GUIDE.md** for:
- Complete variable descriptions
- TLS certificate generation
- Production setup
- Advanced configuration

---

**Created:** 2026-08-30  
**Status:** Ready to use  
**Recommended:** Run `setup-local-env.ps1` (Windows) or `setup-local-env.sh` (Linux/Mac)
