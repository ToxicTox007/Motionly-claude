#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== Task 6: Web and Ingress Boundary Validation ==="
echo "Project Root: ${PROJECT_ROOT}"
echo

# Phase 1: Static configuration tests
echo "[1/5] Running API URL configuration precedence tests..."
cd "${PROJECT_ROOT}/apps/web"
node --test tests/api-url-config-precedence.test.mjs || {
  echo "❌ Configuration tests failed"
  exit 1
}
echo "✅ Configuration tests passed"
echo

# Phase 2: Start services
echo "[2/5] Starting web and ingress services..."
cd "${PROJECT_ROOT}"
docker compose up -d volume-permissions postgres redis temporal-postgres
sleep 5
docker compose up -d temporal supabase-gateway api
sleep 10
docker compose up -d web ingress

echo "Waiting for services to stabilize (30s)..."
sleep 30

docker compose ps
echo "✅ Services started"
echo

# Phase 3: Health checks
echo "[3/5] Verifying service health..."

echo "Checking API health..."
curl -f http://127.0.0.1:8000/health/live || {
  echo "❌ API health check failed"
  docker compose logs api --tail=50
  exit 1
}

echo "Checking web health..."
curl -f http://127.0.0.1:3000/ || {
  echo "❌ Web health check failed"
  docker compose logs web --tail=50
  exit 1
}

echo "✅ Health checks passed"
echo

# Phase 4: Proxy routing validation
echo "[4/5] Verifying proxy routing..."

echo "Testing /api proxy routing..."
curl -f http://127.0.0.1:3000/api/health || {
  echo "❌ /api proxy routing failed (direct to web)"
  docker compose logs web --tail=30
  exit 1
}

# Check if Caddy ingress is configured for local-audit mode
if docker compose ps ingress &>/dev/null; then
  echo "Testing Caddy ingress routing..."
  
  # Test app domain (default: app.audit.localhost)
  APP_DOMAIN="${APP_DOMAIN:-app.audit.localhost}"
  API_DOMAIN="${API_DOMAIN:-api.audit.localhost}"
  SUPABASE_DOMAIN="${SUPABASE_DOMAIN:-supabase.audit.localhost}"
  
  echo "Checking ${APP_DOMAIN} (should route to web:3000)..."
  curl -f -k "https://${APP_DOMAIN}/" || {
    echo "❌ Caddy routing to web failed"
    docker compose logs ingress --tail=30
    exit 1
  }
  
  echo "Checking ${API_DOMAIN} (should route to api:8000)..."
  curl -f -k "https://${API_DOMAIN}/health/live" || {
    echo "❌ Caddy routing to API failed"
    docker compose logs ingress --tail=30
    exit 1
  }
  
  echo "✅ Caddy ingress routing verified"
else
  echo "⚠️  Ingress service not running, skipping Caddy routing tests"
fi

echo "✅ Proxy routing verified"
echo

# Phase 5: Playwright end-to-end tests
echo "[5/5] Running Playwright end-to-end tests..."
cd "${PROJECT_ROOT}/apps/web"

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
  echo "⚠️  node_modules not found, installing dependencies..."
  pnpm install || {
    echo "❌ Failed to install dependencies"
    exit 1
  }
fi

# Check if Playwright browsers are installed
if ! pnpm playwright --version &>/dev/null; then
  echo "⚠️  Playwright not installed, installing..."
  pnpm playwright install chromium || {
    echo "❌ Failed to install Playwright browsers"
    exit 1
  }
fi

pnpm playwright test || {
  echo "❌ E2E tests failed"
  echo "See test-results/ and playwright-report/ for details"
  exit 1
}

echo "✅ E2E tests passed"
echo

echo "=== Task 6 Validation Complete ✅ ==="
echo
echo "Summary:"
echo "  - Configuration precedence tests: ✅"
echo "  - Service health checks: ✅"
echo "  - Proxy routing: ✅"
echo "  - End-to-end tests: ✅"
echo
echo "All web and ingress boundary validations passed successfully."
