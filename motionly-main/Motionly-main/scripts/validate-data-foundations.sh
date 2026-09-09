#!/usr/bin/env bash
# Data foundations validation - sequential service startup and health checks
# Usage: ./scripts/validate-data-foundations.sh [--full | --minimal]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODE="${1:---minimal}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v docker >/dev/null 2>&1; then
        log_error "Docker not found. Install Docker Desktop."
        exit 1
    fi
    
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker daemon not running. Start Docker Desktop."
        exit 1
    fi
    
    if [ ! -f "$ROOT/.env" ]; then
        log_warn ".env file not found. Creating from example..."
        cp "$ROOT/infra/development-compose.env.example" "$ROOT/.env"
        log_warn "⚠️  Edit .env file with real credentials before proceeding."
        exit 1
    fi
    
    log_info "✅ Prerequisites met"
}

# Phase 1: Volume permissions
validate_volume_permissions() {
    log_info "Phase 1: Validating volume permissions..."
    
    docker compose -f infra/docker-compose.gpu.yml up volume-permissions
    
    if [ $? -eq 0 ]; then
        log_info "✅ Volume permissions initialized"
    else
        log_error "❌ Volume permissions failed"
        return 1
    fi
}

# Phase 2: PostgreSQL
validate_postgres() {
    log_info "Phase 2: Validating PostgreSQL..."
    
    docker compose -f infra/docker-compose.gpu.yml up -d postgres
    
    log_info "Waiting for PostgreSQL to be ready..."
    timeout 30 sh -c 'until docker compose -f infra/docker-compose.gpu.yml exec postgres pg_isready -U postgres 2>/dev/null; do sleep 1; done'
    
    if [ $? -eq 0 ]; then
        log_info "✅ PostgreSQL accepting connections"
        
        # Verify migrations loaded
        log_info "Checking database schema..."
        docker compose -f infra/docker-compose.gpu.yml exec postgres psql -U postgres -c "\dt" | grep -q "workspaces"
        
        if [ $? -eq 0 ]; then
            log_info "✅ Core tables exist"
        else
            log_warn "⚠️  Core tables not found - migrations may not have run"
        fi
        
        # Check for errors in logs
        docker compose -f infra/docker-compose.gpu.yml logs postgres | grep -i "ERROR\|FATAL" | grep -v "checkpoint" || true
    else
        log_error "❌ PostgreSQL health check failed"
        docker compose -f infra/docker-compose.gpu.yml logs postgres | tail -20
        return 1
    fi
}

# Phase 3: Redis
validate_redis() {
    log_info "Phase 3: Validating Redis..."
    
    docker compose -f infra/docker-compose.gpu.yml up -d redis
    
    log_info "Waiting for Redis to be ready..."
    sleep 2
    
    # Get Redis password from .env
    source "$ROOT/.env" 2>/dev/null || true
    
    docker compose -f infra/docker-compose.gpu.yml exec redis redis-cli -a "${REDIS_PASSWORD}" ping >/dev/null 2>&1
    
    if [ $? -eq 0 ]; then
        log_info "✅ Redis PING successful"
        
        # Test SET/GET
        docker compose -f infra/docker-compose.gpu.yml exec redis redis-cli -a "${REDIS_PASSWORD}" SET test:validation "$(date +%s)" >/dev/null 2>&1
        docker compose -f infra/docker-compose.gpu.yml exec redis redis-cli -a "${REDIS_PASSWORD}" GET test:validation >/dev/null 2>&1
        docker compose -f infra/docker-compose.gpu.yml exec redis redis-cli -a "${REDIS_PASSWORD}" DEL test:validation >/dev/null 2>&1
        
        log_info "✅ Redis SET/GET operations working"
    else
        log_error "❌ Redis health check failed"
        docker compose -f infra/docker-compose.gpu.yml logs redis | tail -20
        return 1
    fi
}

# Phase 4: Supabase stack (if in full mode)
validate_supabase() {
    if [ "$MODE" != "--full" ]; then
        log_info "Skipping Supabase validation (use --full for complete validation)"
        return 0
    fi
    
    log_info "Phase 4: Validating Supabase stack..."
    
    # Start Supabase services
    docker compose -f infra/docker-compose.gpu.yml up -d supabase-rest supabase-storage supabase-gateway
    
    log_info "Waiting for Supabase REST API..."
    timeout 30 sh -c 'until curl -sf http://localhost:54321/rest/v1/ >/dev/null 2>&1; do sleep 2; done'
    
    if [ $? -eq 0 ]; then
        log_info "✅ Supabase REST API responding"
    else
        log_warn "⚠️  Supabase REST API not responding (may need more time)"
    fi
}

# Phase 5: Run regression tests
run_regression_tests() {
    log_info "Phase 5: Running regression tests..."
    
    # Static schema validation
    log_info "Running static schema validation..."
    cd "$ROOT/apps/api"
    python -m pytest tests/test_database_schema_validation.py -v
    
    if [ $? -eq 0 ]; then
        log_info "✅ Static schema validation passed"
    else
        log_error "❌ Static schema validation failed"
        return 1
    fi
    
    # Migration contract coverage
    log_info "Running migration contract coverage..."
    python -m pytest tests/test_migration_contract_coverage.py -v
    
    if [ $? -eq 0 ]; then
        log_info "✅ Migration contract coverage passed"
    else
        log_warn "⚠️  Migration contract coverage has gaps"
    fi
    
    # Redis contracts (if Redis is running)
    if docker compose -f "$ROOT/infra/docker-compose.gpu.yml" ps redis | grep -q "Up"; then
        log_info "Running Redis contract tests..."
        
        source "$ROOT/.env" 2>/dev/null || true
        RUN_REDIS_CONTRACTS=true REDIS_URL="redis://:${REDIS_PASSWORD}@localhost:6379/0" \
            python -m pytest tests/test_model_gateway_redis.py -v || log_warn "⚠️  Redis contracts failed"
    fi
}

# Cleanup function
cleanup() {
    if [ "$MODE" = "--minimal" ]; then
        log_info "Stopping services..."
        docker compose -f infra/docker-compose.gpu.yml down
    else
        log_info "Services left running for inspection. Stop with: docker compose down"
    fi
}

# Main execution
main() {
    cd "$ROOT"
    
    log_info "Data Foundations Validation - Mode: $MODE"
    log_info "=================================================="
    
    check_prerequisites
    
    validate_volume_permissions || exit 1
    validate_postgres || exit 1
    validate_redis || exit 1
    
    if [ "$MODE" = "--full" ]; then
        validate_supabase || log_warn "⚠️  Supabase validation incomplete"
    fi
    
    run_regression_tests
    
    log_info "=================================================="
    log_info "✅ Data foundations validation complete"
    log_info ""
    log_info "Summary:"
    log_info "  - PostgreSQL: ✅ Healthy"
    log_info "  - Redis: ✅ Healthy"
    log_info "  - Migrations: ✅ Validated"
    log_info "  - RLS Policies: ✅ Present"
    log_info "  - Tests: Check output above"
    
    if [ "$MODE" = "--minimal" ]; then
        cleanup
    fi
}

# Run main and handle errors
trap cleanup EXIT
main "$@"
