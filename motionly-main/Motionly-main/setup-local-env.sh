#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Setting up local development environment ==="
echo ""

# Create infra/.env.local
echo "[1/3] Creating .env.local..."
cat > infra/.env.local << 'EOF'
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
SUPABASE_ANON_KEY=anon-key-base64-encoded-at-least-32-chars-dev
SUPABASE_SERVICE_ROLE_KEY=service-role-key-base64-encoded-at-least-48-chars-dev

# One public origin: /api and /supabase are routed on the app domain
APP_DOMAIN=app.localhost
PUBLIC_ORIGIN=https://app.localhost

# Internal tokens
WORKER_API_TOKEN=dev-worker-token-123
ADMIN_API_TOKEN=dev-admin-token-456

# Temporal
TEMPORAL_BOOTSTRAP_ADMIN_UNTIL=2099-12-31T23:59:59Z

# Inference registry (plan Task 26): the only source of inference routes. Local
# dev runs in fixture mode, so no vendor key is needed; credentials would live
# in the registry secrets bundle, never as vendor env variables.
AI_REGISTRY_FILE=
AI_SECRETS_BUNDLE_FILE=
EOF

echo "✓ Created infra/.env.local"

# Create secrets directory
echo "[2/3] Creating secrets directory..."
mkdir -p infra/secrets
cd infra/secrets

# Create core secret files
echo "devpassword123" > postgres_password
echo "devpassword123" > redis_password
echo "devpassword123" > temporal_db_password
echo "super-secret-jwt-key-at-least-32-characters-long-for-dev" > supabase_jwt_secret
echo "service-role-key-base64-encoded-at-least-48-chars-dev" > supabase_service_role_key
echo "dev-worker-token-123" > worker_api_token
echo "dev-admin-token-456" > admin_api_token
echo "dev-metrics-token" > metrics_bearer_token
# Origin guard secret (32+ characters of A-Z a-z 0-9 _ -); previous stays empty.
echo "dev-origin-verify-0123456789abcdefghijkl" > edge_origin_verify
touch edge_origin_verify_previous

# Create the generic inference secrets bundle file (empty in fixture mode)
touch ai_secrets_bundle

echo "✓ Created core secrets (11 files)"

# Create empty Temporal cert files (for local dev without TLS)
echo "[3/3] Creating Temporal certificate placeholders..."
touch temporal_ca_cert temporal_server_cert temporal_server_key
touch temporal_internode_cert temporal_internode_key
touch temporal_api_cert temporal_api_key
touch temporal_dispatcher_cert temporal_dispatcher_key
touch temporal_worker_cert temporal_worker_key
touch temporal_reconciler_cert temporal_reconciler_key
touch temporal_namespace_bootstrap_cert temporal_namespace_bootstrap_key
touch temporal_diagnostics_cert temporal_diagnostics_key

echo "✓ Created Temporal cert placeholders (17 files)"

# Set permissions (works on Unix-like systems)
if [[ "$OSTYPE" != "msys" && "$OSTYPE" != "win32" ]]; then
  # Containers run as non-root users and Compose mounts secret files with
  # their host mode: read-only files inside a private directory.
  chmod 444 *
  chmod 700 .
  echo "✓ Set secret files to 0444 inside a 0700 directory"
else
  echo "⚠ Skipping chmod on Windows (run 'icacls' manually if needed)"
fi

cd ../..
echo ""
echo "=== Setup Complete! ==="
echo ""
echo "📁 Created:"
echo "  - infra/.env.local (27 environment variables)"
echo "  - infra/secrets/* (31 secret files)"
echo ""
echo "🚀 Start minimal services:"
echo "  docker compose --env-file infra/.env.local -f infra/docker-compose.gpu.yml -f infra/docker-compose.dev.yml up -d postgres redis temporal-postgres"
echo ""
echo "🔧 Verify services:"
echo "  docker compose --env-file infra/.env.local -f infra/docker-compose.gpu.yml ps"
echo ""
echo "📖 See docs/reference/local-development.md for full documentation"
