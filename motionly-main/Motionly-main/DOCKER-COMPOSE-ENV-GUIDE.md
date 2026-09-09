# Docker Compose Environment Configuration Guide

**Purpose:** Complete guide to setting up `.env` file for local development with Docker Compose

---

## Quick Start (Minimal Configuration)

For **local development** with minimal services (postgres, redis, temporal-postgres):

```bash
cd infra
cp development-compose.env.example .env.local
```

Edit `.env.local` with these values:

---

## Required Environment Variables

### 1. Image Tags (Development Mode)

```bash
# Local development images (built from source)
MOTIONLY_API_IMAGE=motionly-api:dev
MOTIONLY_WEB_IMAGE=motionly-web:dev
MOTIONLY_CPU_FFMPEG_WORKER_IMAGE=motionly-cpu-ffmpeg-worker:dev
MOTIONLY_GPU_WORKER_IMAGE=motionly-gpu-worker:dev
MOTIONLY_GPU_FFMPEG_WORKER_IMAGE=motionly-gpu-ffmpeg-worker:dev
MOTIONLY_DEMUCS_WORKER_IMAGE=motionly-demucs-worker:dev
MOTIONLY_PROVIDER_WORKER_IMAGE=motionly-provider-worker:dev
MOTIONLY_PLANNING_WORKER_IMAGE=motionly-planning-worker:dev
MOTIONLY_REMOTION_WORKER_IMAGE=motionly-remotion-worker:dev
MOTIONLY_PUBLISHING_WORKER_IMAGE=motionly-publishing-worker:dev

# Temporal images (use official)
TEMPORAL_ADMIN_TOOLS_IMAGE=temporalio/admin-tools:1.24.2
MOTIONLY_TEMPORAL_SERVER_IMAGE=ghcr.io/motionly/temporal-server:latest
```

### 2. Secrets Directory

```bash
# Directory for secret files (create this)
SECRETS_DIR=./infra/secrets
```

### 3. Database Passwords

```bash
# PostgreSQL main database password
POSTGRES_PASSWORD=local-dev-postgres-password-change-me

# Redis password
REDIS_PASSWORD=local-dev-redis-password-change-me

# Temporal database password
TEMPORAL_DB_PASSWORD=local-dev-temporal-db-password
```

**Tip:** For local dev, simple passwords are fine (e.g., `devpassword123`)

### 4. Supabase Authentication

```bash
# Supabase JWT Secret (used to sign JWTs)
# Generate with: openssl rand -base64 32
SUPABASE_JWT_SECRET=your-random-jwt-secret-at-least-32-characters-long

# Supabase Anonymous Key (public API key)
# Generate with: openssl rand -base64 32
SUPABASE_ANON_KEY=your-random-anon-key-here

# Supabase Service Role Key (admin API key)
# Generate with: openssl rand -base64 48
SUPABASE_SERVICE_ROLE_KEY=your-random-service-role-key-here
```

**Quick Generate All:**
```bash
echo "SUPABASE_JWT_SECRET=$(openssl rand -base64 32)"
echo "SUPABASE_ANON_KEY=$(openssl rand -base64 32)"
echo "SUPABASE_SERVICE_ROLE_KEY=$(openssl rand -base64 48)"
```

### 5. Domain Configuration

```bash
# Application domain (web UI)
APP_DOMAIN=app.localhost

# API domain (backend)
API_DOMAIN=api.localhost

# Supabase domain (auth/storage gateway)
SUPABASE_DOMAIN=supabase.localhost
```

**Note:** These `.localhost` domains automatically resolve to `127.0.0.1`

### 6. Monitoring (Optional)

```bash
# Sentry DSN for error tracking (leave empty for local dev)
SENTRY_DSN=

# Or if you have Sentry:
# SENTRY_DSN=https://your-sentry-dsn@sentry.io/project-id
```

### 7. API Tokens (Internal Communication)

```bash
# Worker API token (for worker → API communication)
WORKER_API_TOKEN=local-dev-worker-token-change-me

# Admin API token (for admin endpoints)
ADMIN_API_TOKEN=local-dev-admin-token-change-me

# Publish token encryption key (at least 32 characters)
PUBLISH_TOKEN_ENCRYPTION_KEY=local-dev-publish-encryption-key-32-chars-minimum
```

### 8. Temporal Configuration

```bash
# Temporal bootstrap admin expiry (RFC3339 timestamp)
# For local dev, set far in future: 2099-12-31T23:59:59Z
TEMPORAL_BOOTSTRAP_ADMIN_UNTIL=2099-12-31T23:59:59Z
```

### 9. External API Keys (Optional - for full features)

```bash
# AI Provider API Keys (leave empty to use fixtures)
GROQ_API_KEY=
OPENROUTER_API_KEY=
RUNWAY_API_KEY=
AZURE_OPENAI_API_KEY=

# If you want to test with real providers:
# GROQ_API_KEY=gsk_your_groq_api_key
# OPENROUTER_API_KEY=sk-or-v1-your_openrouter_key
# etc.
```

---

## Secret Files Required

Create these files in `infra/secrets/` directory:

### Core Secrets (Required for Basic Operation)

```bash
cd infra/secrets

# Database passwords
echo "local-dev-postgres-password-change-me" > postgres_password
echo "local-dev-redis-password-change-me" > redis_password
echo "local-dev-temporal-db-password" > temporal_db_password

# Supabase secrets (use same values as in .env)
echo "your-random-jwt-secret-at-least-32-characters-long" > supabase_jwt_secret
echo "your-random-service-role-key-here" > supabase_service_role_key

# API tokens
echo "local-dev-worker-token-change-me" > worker_api_token
echo "local-dev-admin-token-change-me" > admin_api_token
echo "local-dev-publish-encryption-key-32-chars-minimum" > publish_token_encryption_key
echo "local-dev-oauth-state-secret-change-me" > publish_oauth_state_secret

# Metrics token
echo "local-dev-metrics-token" > metrics_bearer_token

# AI Provider keys (empty files for local dev without providers)
touch groq_api_key
touch openrouter_api_key
touch runway_api_key
touch azure_openai_api_key

# Set proper permissions (0600)
chmod 600 *
```

### Temporal TLS Certificates (Required for Temporal Services)

For local development, you can generate self-signed certificates:

```bash
cd infra/temporal

# Generate CA certificate
openssl req -x509 -newkey rsa:4096 -keyout temporal_ca_key.pem -out temporal_ca_cert.pem -days 365 -nodes -subj "/CN=Temporal CA"

# Generate server certificate
openssl req -newkey rsa:4096 -keyout temporal_server_key.pem -out temporal_server_csr.pem -nodes -subj "/CN=temporal"
openssl x509 -req -in temporal_server_csr.pem -CA temporal_ca_cert.pem -CAkey temporal_ca_key.pem -CAcreateserial -out temporal_server_cert.pem -days 365

# Generate internode certificate (for Temporal internal communication)
openssl req -newkey rsa:4096 -keyout temporal_internode_key.pem -out temporal_internode_csr.pem -nodes -subj "/CN=temporal-internode"
openssl x509 -req -in temporal_internode_csr.pem -CA temporal_ca_cert.pem -CAkey temporal_ca_key.pem -CAcreateserial -out temporal_internode_cert.pem -days 365

# Generate client certificates (for API, dispatcher, worker, etc.)
for CLIENT in api dispatcher worker reconciler namespace_bootstrap diagnostics; do
  openssl req -newkey rsa:4096 -keyout temporal_${CLIENT}_key.pem -out temporal_${CLIENT}_csr.pem -nodes -subj "/CN=temporal-${CLIENT}"
  openssl x509 -req -in temporal_${CLIENT}_csr.pem -CA temporal_ca_cert.pem -CAkey temporal_ca_key.pem -CAcreateserial -out temporal_${CLIENT}_cert.pem -days 365
done

# Move to secrets directory
cd ..
mv temporal/*.pem secrets/

# Clean up CSR files
rm secrets/*_csr.pem

# Set proper permissions
chmod 600 secrets/temporal_*.pem
```

**Or use the simpler approach:** Skip Temporal TLS for local development by creating empty placeholder files:

```bash
cd infra/secrets
touch temporal_ca_cert temporal_server_cert temporal_server_key
touch temporal_internode_cert temporal_internode_key
touch temporal_api_cert temporal_api_key
touch temporal_dispatcher_cert temporal_dispatcher_key
touch temporal_worker_cert temporal_worker_key
touch temporal_reconciler_cert temporal_reconciler_key
touch temporal_namespace_bootstrap_cert temporal_namespace_bootstrap_key
touch temporal_diagnostics_cert temporal_diagnostics_key
chmod 600 temporal_*
```

---

## Complete .env.local Example

Here's a complete example for local development:

```bash
# Image tags (local development builds)
MOTIONLY_API_IMAGE=motionly-api:dev
MOTIONLY_WEB_IMAGE=motionly-web:dev
MOTIONLY_CPU_FFMPEG_WORKER_IMAGE=motionly-cpu-ffmpeg-worker:dev
MOTIONLY_GPU_WORKER_IMAGE=motionly-gpu-worker:dev
MOTIONLY_GPU_FFMPEG_WORKER_IMAGE=motionly-gpu-ffmpeg-worker:dev
MOTIONLY_DEMUCS_WORKER_IMAGE=motionly-demucs-worker:dev
MOTIONLY_PROVIDER_WORKER_IMAGE=motionly-provider-worker:dev
MOTIONLY_PLANNING_WORKER_IMAGE=motionly-planning-worker:dev
MOTIONLY_REMOTION_WORKER_IMAGE=motionly-remotion-worker:dev
MOTIONLY_PUBLISHING_WORKER_IMAGE=motionly-publishing-worker:dev
TEMPORAL_ADMIN_TOOLS_IMAGE=temporalio/admin-tools:1.24.2
MOTIONLY_TEMPORAL_SERVER_IMAGE=ghcr.io/motionly/temporal-server:latest

# Secrets directory
SECRETS_DIR=./infra/secrets

# Database passwords
POSTGRES_PASSWORD=devpassword123
REDIS_PASSWORD=devpassword123
TEMPORAL_DB_PASSWORD=devpassword123

# Supabase authentication
SUPABASE_JWT_SECRET=super-secret-jwt-key-at-least-32-characters-long-for-dev
SUPABASE_ANON_KEY=anon-key-base64-encoded-at-least-32-chars
SUPABASE_SERVICE_ROLE_KEY=service-role-key-base64-encoded-at-least-48-chars

# Domains
APP_DOMAIN=app.localhost
API_DOMAIN=api.localhost
SUPABASE_DOMAIN=supabase.localhost

# Monitoring (optional)
SENTRY_DSN=

# Internal tokens
WORKER_API_TOKEN=dev-worker-token-123
ADMIN_API_TOKEN=dev-admin-token-456
PUBLISH_TOKEN_ENCRYPTION_KEY=dev-publish-encryption-key-32-chars-minimum-here

# Temporal
TEMPORAL_BOOTSTRAP_ADMIN_UNTIL=2099-12-31T23:59:59Z

# External API keys (optional)
GROQ_API_KEY=
OPENROUTER_API_KEY=
RUNWAY_API_KEY=
AZURE_OPENAI_API_KEY=
```

---

## Quick Setup Script

Save this as `setup-local-env.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/infra"

echo "=== Setting up local development environment ==="

# Create .env.local
cat > .env.local << 'EOF'
MOTIONLY_API_IMAGE=motionly-api:dev
MOTIONLY_WEB_IMAGE=motionly-web:dev
MOTIONLY_CPU_FFMPEG_WORKER_IMAGE=motionly-cpu-ffmpeg-worker:dev
MOTIONLY_GPU_WORKER_IMAGE=motionly-gpu-worker:dev
MOTIONLY_GPU_FFMPEG_WORKER_IMAGE=motionly-gpu-ffmpeg-worker:dev
MOTIONLY_DEMUCS_WORKER_IMAGE=motionly-demucs-worker:dev
MOTIONLY_PROVIDER_WORKER_IMAGE=motionly-provider-worker:dev
MOTIONLY_PLANNING_WORKER_IMAGE=motionly-planning-worker:dev
MOTIONLY_REMOTION_WORKER_IMAGE=motionly-remotion-worker:dev
MOTIONLY_PUBLISHING_WORKER_IMAGE=motionly-publishing-worker:dev
TEMPORAL_ADMIN_TOOLS_IMAGE=temporalio/admin-tools:1.24.2
MOTIONLY_TEMPORAL_SERVER_IMAGE=ghcr.io/motionly/temporal-server:latest
SECRETS_DIR=./infra/secrets
POSTGRES_PASSWORD=devpassword123
REDIS_PASSWORD=devpassword123
TEMPORAL_DB_PASSWORD=devpassword123
SUPABASE_JWT_SECRET=super-secret-jwt-key-at-least-32-characters-long-for-dev
SUPABASE_ANON_KEY=anon-key-base64-encoded-at-least-32-chars-dev
SUPABASE_SERVICE_ROLE_KEY=service-role-key-base64-encoded-at-least-48-chars-dev
APP_DOMAIN=app.localhost
API_DOMAIN=api.localhost
SUPABASE_DOMAIN=supabase.localhost
SENTRY_DSN=
WORKER_API_TOKEN=dev-worker-token-123
ADMIN_API_TOKEN=dev-admin-token-456
PUBLISH_TOKEN_ENCRYPTION_KEY=dev-publish-encryption-key-32-chars-minimum-here
TEMPORAL_BOOTSTRAP_ADMIN_UNTIL=2099-12-31T23:59:59Z
GROQ_API_KEY=
OPENROUTER_API_KEY=
RUNWAY_API_KEY=
AZURE_OPENAI_API_KEY=
EOF

echo "✓ Created .env.local"

# Create secrets directory
mkdir -p secrets
cd secrets

# Create secret files
echo "devpassword123" > postgres_password
echo "devpassword123" > redis_password
echo "devpassword123" > temporal_db_password
echo "super-secret-jwt-key-at-least-32-characters-long-for-dev" > supabase_jwt_secret
echo "service-role-key-base64-encoded-at-least-48-chars-dev" > supabase_service_role_key
echo "dev-worker-token-123" > worker_api_token
echo "dev-admin-token-456" > admin_api_token
echo "dev-publish-encryption-key-32-chars-minimum-here" > publish_token_encryption_key
echo "dev-oauth-state-secret" > publish_oauth_state_secret
echo "dev-metrics-token" > metrics_bearer_token

# Create empty API key files
touch groq_api_key openrouter_api_key runway_api_key azure_openai_api_key

# Create empty Temporal cert files (for local dev without TLS)
touch temporal_ca_cert temporal_server_cert temporal_server_key
touch temporal_internode_cert temporal_internode_key
touch temporal_api_cert temporal_api_key
touch temporal_dispatcher_cert temporal_dispatcher_key
touch temporal_worker_cert temporal_worker_key
touch temporal_reconciler_cert temporal_reconciler_key
touch temporal_namespace_bootstrap_cert temporal_namespace_bootstrap_key
touch temporal_diagnostics_cert temporal_diagnostics_key

# Set permissions
chmod 600 *

cd ..
echo "✓ Created secrets directory with all required files"

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "Start services with:"
echo "  docker compose --env-file infra/.env.local -f infra/docker-compose.gpu.yml -f infra/docker-compose.dev.yml up -d postgres redis"
echo ""
echo "Or for full stack:"
echo "  docker compose --env-file infra/.env.local -f infra/docker-compose.gpu.yml -f infra/docker-compose.dev.yml up -d"
```

Make it executable and run:

```bash
chmod +x setup-local-env.sh
./setup-local-env.sh
```

---

## Starting Services

### Minimal Stack (Data Foundations Testing)

```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d postgres redis temporal-postgres
```

### With Temporal

```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d postgres redis temporal-postgres temporal
```

### Full Stack (All Services)

```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d --build
```

---

## Troubleshooting

### Error: "required variable X is missing a value"

**Solution:** Add the variable to your `.env.local` file

### Error: "secrets.X.file: required variable SECRETS_DIR is missing"

**Solution:** Set `SECRETS_DIR=./infra/secrets` in `.env.local`

### Error: "no such file or directory: infra/secrets/postgres_password"

**Solution:** Create the secret file:
```bash
echo "devpassword123" > infra/secrets/postgres_password
chmod 600 infra/secrets/postgres_password
```

### Services fail with "connection refused"

**Solution:** Start services in dependency order:
1. postgres, redis, temporal-postgres
2. temporal, supabase-*
3. api, web, workers

### Temporal won't start

**Solution:** If TLS is causing issues, create empty cert files:
```bash
cd infra/secrets
touch temporal_ca_cert temporal_server_cert temporal_server_key
chmod 600 temporal_*
```

---

## Summary

**Minimum Required:**
- 10 image tags
- 1 secrets directory path
- 3 database passwords
- 3 Supabase auth keys
- 3 domain names
- 3 internal API tokens
- 1 Temporal bootstrap time
- 31 secret files (12 core + 19 Temporal certs)

**Total:** ~23 environment variables + 31 secret files

**Recommended Approach:** Use the quick setup script above to create everything with development defaults.

---

**Created:** 2026-08-30  
**For:** Local development environment setup  
**Status:** Ready to use
