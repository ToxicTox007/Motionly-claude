# PowerShell script to set up local development environment
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "=== Setting up local development environment ===" -ForegroundColor Cyan
Write-Host ""

# Create infra/.env.local
Write-Host "[1/3] Creating .env.local..." -ForegroundColor Yellow
$envContent = @"
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
SUPABASE_ANON_KEY=anon-key-base64-encoded-at-least-32-chars-dev
SUPABASE_SERVICE_ROLE_KEY=service-role-key-base64-encoded-at-least-48-chars-dev

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

# External API keys (optional - leave empty for fixture mode)
GROQ_API_KEY=
OPENROUTER_API_KEY=
RUNWAY_API_KEY=
AZURE_OPENAI_API_KEY=
"@

$envContent | Out-File -FilePath "infra\.env.local" -Encoding UTF8 -NoNewline
Write-Host "[OK] Created infra/.env.local" -ForegroundColor Green

# Create secrets directory
Write-Host "[2/3] Creating secrets directory..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "infra\secrets" | Out-Null
Set-Location "infra\secrets"

# Create core secret files
"devpassword123" | Out-File -FilePath "postgres_password" -Encoding UTF8 -NoNewline
"devpassword123" | Out-File -FilePath "redis_password" -Encoding UTF8 -NoNewline
"devpassword123" | Out-File -FilePath "temporal_db_password" -Encoding UTF8 -NoNewline
"super-secret-jwt-key-at-least-32-characters-long-for-dev" | Out-File -FilePath "supabase_jwt_secret" -Encoding UTF8 -NoNewline
"service-role-key-base64-encoded-at-least-48-chars-dev" | Out-File -FilePath "supabase_service_role_key" -Encoding UTF8 -NoNewline
"dev-worker-token-123" | Out-File -FilePath "worker_api_token" -Encoding UTF8 -NoNewline
"dev-admin-token-456" | Out-File -FilePath "admin_api_token" -Encoding UTF8 -NoNewline
"dev-publish-encryption-key-32-chars-minimum-here" | Out-File -FilePath "publish_token_encryption_key" -Encoding UTF8 -NoNewline
"dev-oauth-state-secret" | Out-File -FilePath "publish_oauth_state_secret" -Encoding UTF8 -NoNewline
"dev-metrics-token" | Out-File -FilePath "metrics_bearer_token" -Encoding UTF8 -NoNewline

# Create empty API key files
New-Item -ItemType File -Force -Path "groq_api_key" | Out-Null
New-Item -ItemType File -Force -Path "openrouter_api_key" | Out-Null
New-Item -ItemType File -Force -Path "runway_api_key" | Out-Null
New-Item -ItemType File -Force -Path "azure_openai_api_key" | Out-Null

Write-Host "[OK] Created core secrets (14 files)" -ForegroundColor Green

# Create empty Temporal cert files
Write-Host "[3/3] Creating Temporal certificate placeholders..." -ForegroundColor Yellow
$temporalCerts = @(
    "temporal_ca_cert", "temporal_server_cert", "temporal_server_key",
    "temporal_internode_cert", "temporal_internode_key",
    "temporal_api_cert", "temporal_api_key",
    "temporal_dispatcher_cert", "temporal_dispatcher_key",
    "temporal_worker_cert", "temporal_worker_key",
    "temporal_reconciler_cert", "temporal_reconciler_key",
    "temporal_namespace_bootstrap_cert", "temporal_namespace_bootstrap_key",
    "temporal_diagnostics_cert", "temporal_diagnostics_key"
)

foreach ($cert in $temporalCerts) {
    New-Item -ItemType File -Force -Path $cert | Out-Null
}

Write-Host "[OK] Created Temporal cert placeholders (17 files)" -ForegroundColor Green

Set-Location ..\..

Write-Host ""
Write-Host "=== Setup Complete! ===" -ForegroundColor Green
Write-Host ""
Write-Host "Created:" -ForegroundColor Cyan
Write-Host "  infra/.env.local with 23 environment variables"
Write-Host "  infra/secrets/* with 31 secret files"
Write-Host ""
Write-Host "Start minimal services:" -ForegroundColor Yellow
Write-Host "  docker compose --env-file infra/.env.local -f infra/docker-compose.gpu.yml -f infra/docker-compose.dev.yml up -d postgres redis temporal-postgres"
Write-Host ""
Write-Host "Verify services:" -ForegroundColor Yellow
Write-Host "  docker compose --env-file infra/.env.local -f infra/docker-compose.gpu.yml ps"
Write-Host ""
Write-Host "See docs/reference/local-development.md for full documentation" -ForegroundColor Cyan
