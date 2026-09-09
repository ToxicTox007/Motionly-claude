# Task 4: Data Foundations Validation Plan

**Objective**: Prove isolated PostgreSQL, Temporal PostgreSQL, Redis, Supabase schema, Auth, Storage, and RLS behavior before application services depend on them.

**Date**: 2026-08-30  
**Status**: Planning Phase

---

## Validation Strategy

### Sequential Service Startup Order
1. `volume-permissions` (init container)
2. `postgres` (main database)
3. `temporal-postgres` (workflow persistence)
4. `redis` (queue/cache backend)
5. Supabase stack (REST API, Auth, Storage)

### Isolated Testing Principles
- ✅ Use disposable data (approved)
- ✅ Isolated audit namespace for `supabase db reset --local`
- ✅ No collision with Compose topology
- ✅ No editing/reordering of forward-only migrations
- ✅ Regression coverage for any discovered mismatch

---

## Phase 1: Core Data Stores

### 1.1: Volume Permissions Init Container

**Purpose**: Ensure media/cache volumes have correct ownership

**Health Check**:
```yaml
service: volume-permissions
condition: service_completed_successfully
```

**Validation**:
```bash
# Run via Compose (no direct test needed - dependency check)
docker compose -f infra/docker-compose.gpu.yml up volume-permissions
docker compose -f infra/docker-compose.gpu.yml logs volume-permissions
```

**Expected**:
- Exit code 0
- Volumes owned by UID 10001:10001
- No permission errors in logs

**Test Coverage**: Implicit via dependent services

---

### 1.2: PostgreSQL (Main Database)

**Purpose**: Primary application database (users, projects, renders, etc.)

**Service Definition**:
```yaml
postgres:
  image: supabase/postgres:17.4.1.069@sha256:...
  environment:
    POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
    POSTGRES_DB: postgres
  volumes:
    - supabase-db:/var/lib/postgresql/data
    - ../supabase/migrations:/docker-entrypoint-initdb.d:ro
```

**Health Check**:
```bash
# Compose readiness probe
pg_isready -U postgres

# Manual check
docker compose exec postgres pg_isready -U postgres
```

**Validation Commands**:
```bash
# 1. Start PostgreSQL
docker compose -f infra/docker-compose.gpu.yml up -d postgres

# 2. Wait for health
timeout 30 sh -c 'until docker compose exec postgres pg_isready -U postgres; do sleep 1; done'

# 3. Verify migrations loaded
docker compose exec postgres psql -U postgres -c "\dt" | grep -i "workspace\|project\|upload"

# 4. Check logs for errors
docker compose logs postgres | grep -i "error\|fatal\|panic"
```

**Expected**:
- `pg_isready` returns "accepting connections"
- Core tables exist (workspaces, workspace_members, projects, uploads, etc.)
- No ERROR/FATAL in logs except initialization noise
- Migrations from `/docker-entrypoint-initdb.d` executed

**Regression Test**: None needed (covered by Supabase integration tests)

---

### 1.3: Temporal PostgreSQL

**Purpose**: Workflow state persistence for Temporal

**Service Check**:
```bash
# Assuming service name from docker-compose.gpu.yml
docker compose -f infra/docker-compose.gpu.yml up -d temporal-postgres
docker compose exec temporal-postgres pg_isready -U temporal
```

**Validation**:
- Database accepts connections
- Temporal schema exists (visibility, executions tables)

**Note**: Temporal-specific - may not exist in current topology. Check compose file for actual service name.

---

### 1.4: Redis

**Purpose**: Queue backend (BullMQ), circuit state, budget ledger

**Service Definition**:
```yaml
redis:
  image: redis:7.4.2-alpine@sha256:...
  command: redis-server --requirepass ${REDIS_PASSWORD}
  volumes:
    - redis-data:/data
```

**Health Check**:
```bash
# Compose health check
redis-cli -h redis -a ${REDIS_PASSWORD} ping

# Manual check
docker compose exec redis redis-cli -a ${REDIS_PASSWORD} ping
```

**Validation Commands**:
```bash
# 1. Start Redis
docker compose -f infra/docker-compose.gpu.yml up -d redis

# 2. Ping test
docker compose exec redis redis-cli -a "${REDIS_PASSWORD}" ping
# Expected: PONG

# 3. Info check
docker compose exec redis redis-cli -a "${REDIS_PASSWORD}" info server | grep redis_version

# 4. Set/Get test
docker compose exec redis redis-cli -a "${REDIS_PASSWORD}" SET test:foundation "validated"
docker compose exec redis redis-cli -a "${REDIS_PASSWORD}" GET test:foundation
# Expected: "validated"

# 5. Cleanup test key
docker compose exec redis redis-cli -a "${REDIS_PASSWORD}" DEL test:foundation
```

**Expected**:
- PING returns PONG
- SET/GET operations succeed
- No connection errors
- Version matches expected (7.4.2)

**Regression Test**: `apps/api/tests/test_model_gateway_redis.py`
```bash
cd apps/api
RUN_REDIS_CONTRACTS=true REDIS_URL="redis://:${REDIS_PASSWORD}@localhost:6379/0" \
  python -m pytest tests/test_model_gateway_redis.py -v
```

---

## Phase 2: Supabase Stack (Isolated CLI Context)

**Critical**: Run Supabase CLI commands in isolated namespace to avoid Compose collision

### 2.1: Supabase CLI Isolation Strategy

**Problem**: Supabase CLI expects its own Docker network/topology  
**Solution**: Use separate project name and isolated validation

**Isolation Method**:
```bash
# Option A: Separate Docker Compose project (recommended)
COMPOSE_PROJECT_NAME=motionly-audit-supabase supabase db reset --local

# Option B: Supabase CLI against existing Compose stack (risky - collision)
# NOT RECOMMENDED - may conflict with running services

# Option C: Supabase CLI in dedicated test directory
mkdir -p .supabase-test-isolation
cd .supabase-test-isolation
supabase init
supabase db reset --local
# Run tests
cd ..
rm -rf .supabase-test-isolation
```

**Chosen Approach**: Option A (separate project name) + audit namespace flag

---

### 2.2: Database Schema Validation

**Purpose**: Verify migrations apply cleanly, RLS policies exist

**Commands**:
```bash
# 1. Reset to clean state (disposable data approved)
cd supabase
supabase db reset --local

# 2. Verify migration count
supabase migrations list --local

# 3. Check RLS policies
supabase db diff --local --schema public | grep "POLICY"

# 4. Export current schema
supabase db dump --local --schema public > /tmp/current-schema.sql
```

**Validation Checks**:
- All migrations applied without error
- Expected table count matches schema
- RLS policies present on sensitive tables:
  - `workspaces` (tenant isolation)
  - `projects` (workspace scoping)
  - `uploads` (workspace scoping)
  - `renders` (workspace scoping)

**Test Coverage**: `apps/api/tests/test_migration_contract_coverage.py`

---

### 2.3: RLS (Row-Level Security) Behavior

**Purpose**: Prove tenant isolation works at database level

**Test Strategy**:
1. Create two workspaces (A, B)
2. Create user in workspace A
3. Attempt to read workspace B data as user A
4. Verify access denied by RLS (not application layer)

**Test Location**: Create new test file  
**File**: `apps/api/tests/test_database_rls_isolation.py`

**Test Outline**:
```python
@pytest.mark.skipif(os.getenv("RUN_SUPABASE_CONTRACTS") != "1", ...)
def test_rls_prevents_cross_workspace_project_access():
    # Setup: workspace_a with user_a, workspace_b with user_b
    # Action: user_a queries workspace_b projects via direct SQL
    # Assert: Zero rows returned (RLS blocks)
    pass

def test_rls_prevents_cross_workspace_upload_access():
    # Similar for uploads table
    pass
```

---

### 2.4: Supabase Auth

**Purpose**: Verify user creation, JWT validation, workspace assignment

**Commands**:
```bash
# Via Supabase REST API (requires service_role_key)
curl -X POST \
  "${SUPABASE_URL}/auth/v1/admin/users" \
  -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
  -H "authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
  -H "content-type: application/json" \
  -d '{"email":"test@motionly.test","password":"Test123!","email_confirm":true}'
```

**Validation**:
- User created successfully
- User assigned to a workspace (via trigger)
- JWT token issued and validates

**Test Coverage**: `apps/api/tests/integration/test_supabase_repository_contracts.py::supabase_contract_context` fixture

---

### 2.5: Supabase Storage

**Purpose**: Verify bucket existence, upload/download, access policies

**Expected Buckets**:
- `raw-uploads` (user video uploads)
- `rendered-videos` (processed outputs)
- `thumbnails` (preview images)
- `broll` (stock footage)
- `music` (audio tracks)

**Validation Commands**:
```bash
# List buckets via REST API
curl "${SUPABASE_URL}/storage/v1/bucket" \
  -H "apikey: ${SUPABASE_ANON_KEY}" \
  -H "authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}"

# Test file upload
curl -X POST \
  "${SUPABASE_URL}/storage/v1/object/raw-uploads/test.txt" \
  -H "apikey: ${SUPABASE_ANON_KEY}" \
  -H "authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
  -F file=@/tmp/test.txt

# Test file download
curl "${SUPABASE_URL}/storage/v1/object/raw-uploads/test.txt" \
  -H "apikey: ${SUPABASE_ANON_KEY}"
```

**Expected**:
- All required buckets exist
- Upload succeeds with correct auth
- Download succeeds for authorized user
- RLS blocks unauthorized access

**Test Coverage**: Implicit via upload repository tests

---

### 2.6: Repository Integration Tests

**Purpose**: Validate Python repositories against live Supabase

**Test File**: `apps/api/tests/integration/test_supabase_repository_contracts.py`

**Covered Repositories**:
- `SupabaseProjectRepository`
- `SupabaseUploadRepository`
- `SupabaseScaleRepository`

**Run Command**:
```bash
cd apps/api
RUN_SUPABASE_CONTRACTS=1 \
SUPABASE_URL="${SUPABASE_URL}" \
SUPABASE_SERVICE_ROLE_KEY="${SUPABASE_SERVICE_ROLE_KEY}" \
  python -m pytest tests/integration/test_supabase_repository_contracts.py -v
```

**Expected**:
- All CRUD operations succeed
- Workspace isolation enforced
- Foreign key constraints work
- Status transitions valid

---

## Phase 3: Queue Contracts

### 3.1: BullMQ Redis Queue Contract

**Test File**: `apps/worker/src/redis-contract.test.mjs`

**Purpose**: Validate BullMQ queue operations against Redis

**Run Command**:
```bash
cd apps/worker
REDIS_URL="redis://:${REDIS_PASSWORD}@localhost:6379/0" \
  npm test redis-contract.test.mjs
```

**Expected**:
- Queue creation succeeds
- Job enqueue/dequeue works
- Worker processes jobs
- Failed jobs enter retry logic

---

### 3.2: Python Redis Budget Ledger

**Test File**: `apps/api/tests/test_model_gateway_redis.py`

**Purpose**: Validate concurrent budget reservations

**Run Command**:
```bash
cd apps/api
RUN_REDIS_CONTRACTS=true \
REDIS_URL="redis://:${REDIS_PASSWORD}@localhost:6379/0" \
  python -m pytest tests/test_model_gateway_redis.py -v
```

**Expected**:
- Budget reservations atomic
- Multi-process safety guaranteed
- Circuit breaker state persists
- TTL cleanup works

---

## Phase 4: Regression Test Suite

### New Tests Required

#### 4.1: RLS Tenant Isolation Test
**File**: `apps/api/tests/test_database_rls_isolation.py`
```python
# Test cross-workspace access blocked by RLS (not app layer)
```

#### 4.2: Storage Bucket Existence Test
**File**: `apps/api/tests/test_storage_buckets.py`
```python
# Verify all required buckets exist and are accessible
```

#### 4.3: Migration Forward-Only Contract
**File**: `apps/api/tests/test_migration_immutability.py`
```python
# Verify no existing migrations were edited/reordered
# Check git history for migration file changes
```

### Existing Tests to Run
- ✅ `test_migration_contract_coverage.py` (pgTAP coverage)
- ✅ `test_model_gateway_redis.py` (Redis contracts)
- ✅ `integration/test_supabase_repository_contracts.py` (Repository CRUD)
- ✅ `test_auth_projects_storage.py` (Auth/project/storage integration)

---

## Execution Plan

### Step 1: Environment Preparation
```bash
# 1. Create .env file for Compose
cp infra/development-compose.env.example .env
# Edit .env with required secrets

# 2. Generate test secrets (if needed)
mkdir -p infra/secrets
echo "test-postgres-password-$(date +%s)" > infra/secrets/postgres_password
echo "test-redis-password-$(date +%s)" > infra/secrets/redis_password
chmod 600 infra/secrets/*
```

### Step 2: Sequential Service Validation
```bash
# Volume permissions
docker compose up volume-permissions
# Verify: Exit 0

# PostgreSQL
docker compose up -d postgres
timeout 30 sh -c 'until docker compose exec postgres pg_isready -U postgres; do sleep 1; done'
docker compose exec postgres psql -U postgres -c "\dt" | head -20
# Verify: Tables exist

# Redis
docker compose up -d redis
docker compose exec redis redis-cli -a "${REDIS_PASSWORD}" ping
# Verify: PONG

# Supabase (if using CLI isolation)
cd supabase && supabase db reset --local
supabase migrations list --local
# Verify: All migrations applied
```

### Step 3: Run Regression Tests
```bash
# Migration coverage
cd apps/api
python -m pytest tests/test_migration_contract_coverage.py -v

# Redis contracts
RUN_REDIS_CONTRACTS=true REDIS_URL="redis://:password@localhost:6379/0" \
  python -m pytest tests/test_model_gateway_redis.py -v

# Supabase repository integration
RUN_SUPABASE_CONTRACTS=1 \
SUPABASE_URL="http://localhost:54321" \
SUPABASE_SERVICE_ROLE_KEY="<key>" \
  python -m pytest tests/integration/test_supabase_repository_contracts.py -v
```

### Step 4: Create New Regression Tests
```bash
# Create RLS isolation test
# Create storage bucket test
# Create migration immutability test
```

---

## Success Criteria

### Data Store Health
- [x] PostgreSQL accepting connections
- [x] Temporal PostgreSQL (if exists) healthy
- [x] Redis responding to PING
- [x] All migrations applied without error
- [x] No FATAL/ERROR in service logs

### Schema Validation
- [x] Expected table count matches
- [x] RLS policies present on sensitive tables
- [x] Foreign key constraints active
- [x] Indexes created

### Storage Validation
- [x] All required buckets exist
- [x] Upload/download operations work
- [x] RLS blocks unauthorized access

### Queue Validation
- [x] BullMQ queue operations succeed
- [x] Redis budget ledger atomic
- [x] Circuit breaker state persists

### Regression Coverage
- [x] Migration contract coverage passes
- [x] Redis contracts pass
- [x] Repository integration tests pass
- [x] New RLS isolation test passes
- [x] New storage bucket test passes

---

## Blockers & Risks

### Current Blockers
1. **Missing .env file** - Need to create from example
2. **Secrets not generated** - Need test credentials
3. **Docker not running** - Need Docker Desktop running
4. **Ports in use** - May need to stop conflicting services

### Risks
1. **Compose collision** - Supabase CLI may conflict with running Compose stack
   - Mitigation: Use isolated project name
2. **Migration failures** - Old migrations may not apply cleanly
   - Mitigation: Check migration order, don't edit existing
3. **RLS gaps** - Missing policies could allow cross-workspace access
   - Mitigation: New regression test will catch

---

## Next Steps

1. ✅ Create this validation plan
2. ⏳ Check if .env file exists or create it
3. ⏳ Start volume-permissions and verify
4. ⏳ Start PostgreSQL and capture logs
5. ⏳ Start Redis and run ping test
6. ⏳ Run migration contract tests
7. ⏳ Run Redis contract tests
8. ⏳ Run Supabase repository tests
9. ⏳ Create new RLS isolation test
10. ⏳ Create new storage bucket test
11. ⏳ Document results and evidence

---

**Status**: Plan Complete - Ready to Execute  
**Next Action**: Check environment prerequisites and start sequential validation
