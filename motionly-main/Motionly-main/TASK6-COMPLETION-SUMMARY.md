# Task 6 Completion Summary: Web and Ingress Boundary Validation

**Date:** 2026-08-30  
**Status:** ✅ Static analysis complete | ⏸️ Runtime validation blocked (node_modules + Docker)

---

## Objective

Prove the browser-facing application works through its intended same-origin proxy, authentication, API, and Supabase paths.

---

## Deliverables Completed

### 1. Configuration Analysis ✅

**File:** `TASK6-WEB-INGRESS-VALIDATION.md`

- Analyzed `NEXT_PUBLIC_API_BASE_URL` (canonical) vs `NEXT_PUBLIC_API_URL` (legacy) precedence
- Documented browser same-origin proxy resolution (`/api` and `/supabase`)
- Mapped Caddy routing architecture (simple reverse proxy)
- Documented security headers (CSP, X-Frame-Options, Permissions-Policy)
- Mapped authentication middleware flow and protected routes

**Key Finding:** Canonical `NEXT_PUBLIC_API_BASE_URL` takes precedence over legacy `NEXT_PUBLIC_API_URL` via nullish coalescing (`??`) at `apps/web/lib/env.ts:83`

### 2. Table-Driven Configuration Tests ✅

**File:** `apps/web/tests/api-url-config-precedence.test.mjs`

**Coverage:** 14 test cases

| Test Scenario | Purpose |
|---------------|---------|
| canonical-only | Verify `NEXT_PUBLIC_API_BASE_URL` alone succeeds |
| legacy-only | Verify `NEXT_PUBLIC_API_URL` alone succeeds |
| both-present | Verify canonical takes precedence when both set |
| missing-both | Verify error thrown when neither set |
| local-proxy | Verify browser resolves to `/api` same-origin path |
| production-localhost-rejected | Verify production rejects `localhost` URLs |
| production-127-rejected | Verify production rejects `127.0.0.1` URLs |
| production-invalid-domain-rejected | Verify production rejects `.invalid` TLD |
| development-localhost-allowed | Verify development allows `localhost` URLs |
| production-build-phase | Verify build phase allows `.invalid` fallback |
| browser-client-proxy | Verify `isClient=true` uses `/api` path |
| next.config.ts-fallback | Verify fallback to `localhost:8000` |
| docker-compose-canonical | Verify production uses canonical variable |
| env-example-documentation | Verify `.env.example` documents alias |

**Status:** Tests created, blocked by missing `node_modules` (zod package required)

### 3. Validation Script ✅

**File:** `scripts/validate-web-ingress.sh`

**Phases:**
1. Static configuration tests (precedence, fallbacks, production restrictions)
2. Service startup (web, API, ingress)
3. Health checks (API `/health/live`, web `/`)
4. Proxy routing validation (`/api/*`, `/supabase/*`, Caddy ingress)
5. Playwright end-to-end tests (auth, workspace, project journeys)

**Status:** Script created, ready for Docker + node_modules

### 4. Architectural Documentation ✅

**Routing Layers:**

```
┌─────────────────────────────────────────────────────────────┐
│ Ingress Layer (Caddy)                                       │
│  - app.audit.localhost     → web:3000                       │
│  - api.audit.localhost     → api:8000                       │
│  - supabase.audit.localhost → supabase-gateway:8000         │
│  - TLS internal (local CA, no ACME)                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Application Layer (Next.js)                                 │
│  - Browser requests /api/* → proxy to http://127.0.0.1:8000 │
│  - Browser requests /supabase/* → proxy to Supabase URL     │
│  - Server-side: uses actual backend URLs (not /api proxy)   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Authentication Middleware (proxy.ts)                        │
│  - Protected: /dashboard, /projects, /billing, /account     │
│  - Supabase SSR client validates session                    │
│  - Redirects to /login if unauthenticated                   │
└─────────────────────────────────────────────────────────────┘
```

**Security Headers:**
- `Content-Security-Policy`: Restrictive (`default-src 'self'`), includes API/Supabase origins in `connect-src`
- `X-Frame-Options: DENY`: Clickjacking protection
- `X-Content-Type-Options: nosniff`: MIME-sniffing protection
- `Permissions-Policy`: Disables camera, mic, geolocation, payment, USB
- `Referrer-Policy: strict-origin-when-cross-origin`: Minimal referrer leakage

---

## Key Technical Findings

### 1. API URL Precedence (Canonical vs Legacy)

**Precedence Rule:**
```typescript
// apps/web/lib/env.ts:83
const apiBaseUrl = isClient
  ? "/api"
  : (parsedEnv.NEXT_PUBLIC_API_BASE_URL ?? parsedEnv.NEXT_PUBLIC_API_URL);
```

**Evidence:**
- Canonical `NEXT_PUBLIC_API_BASE_URL` takes precedence via `??` operator
- Legacy `NEXT_PUBLIC_API_URL` used as fallback for Railway/Render dashboard compatibility
- Production deployment (`infra/docker-compose.gpu.yml:408`) uses **canonical only**
- `.env.example` documents legacy as commented alias

**Requirement:** Both variables preserved until separately approved removal release (per spec)

### 2. Browser Proxy Resolution

**Client-Side:**
```typescript
// apps/web/lib/env.ts:82-83
const apiBaseUrl = isClient ? "/api" : actualURL;
```

**Same-Origin Benefits:**
- No CORS preflight requests (`OPTIONS`)
- No `Access-Control-Allow-Origin` headers needed
- Credentials (cookies, auth tokens) automatically included
- CSP compliance (`'self'` directive covers `/api` and `/supabase`)

**Server-Side (SSR/SSG):**
- Uses actual backend URLs for data fetching
- Not bound by browser same-origin policy
- Can make direct cross-origin requests

### 3. Production Security Validation

**Implementation** (`apps/web/lib/env.ts:46-70`):

```typescript
if (process.env.NODE_ENV === "production" && !isProductionBuild) {
  // Block auth bypass
  if (skipAuth === "true" || skipAuthPublic === "true") {
    throw new Error("Security violation: SKIP_AUTH_MIDDLEWARE not allowed in production");
  }
  
  // Validate production URLs
  for (const [name, value] of [
    ["NEXT_PUBLIC_API_BASE_URL or NEXT_PUBLIC_API_URL", canonical ?? legacy],
    // ... other URLs
  ]) {
    if (/(localhost|127\.0\.0\.1|0\.0\.0\.0|\.invalid)/i.test(value)) {
      throw new Error(`${name} must be a deployed production URL`);
    }
  }
}
```

**Rejected Patterns:**
- `localhost` (any port)
- `127.0.0.1` (loopback)
- `0.0.0.0` (wildcard bind)
- `.invalid` TLD (RFC 2606 reserved)

**Allowed in Development:** All patterns above valid for `NODE_ENV=development`

### 4. Caddy Ingress Routing

**Local Audit Mode** (`infra/Caddyfile.local-audit`):
```caddyfile
{$APP_DOMAIN:app.audit.localhost} {
  tls internal
  reverse_proxy web:3000
}

{$API_DOMAIN:api.audit.localhost} {
  tls internal
  reverse_proxy api:8000
}

{$SUPABASE_DOMAIN:supabase.audit.localhost} {
  tls internal
  reverse_proxy supabase-gateway:8000
}
```

**Properties:**
- **TLS Internal:** Caddy's local CA (not ACME)
- **Loopback Domains:** `.audit.localhost` resolves to `127.0.0.1`
- **Simple Reverse Proxy:** No path manipulation, straight passthrough
- **No ACME:** Never obtains public certificates

### 5. Authentication Flow

**Protected Routes:** `/dashboard/*`, `/projects/*`, `/billing/*`, `/account/*`

**Middleware** (`apps/web/proxy.ts`):
1. Check if route is protected
2. If `SKIP_AUTH_MIDDLEWARE=true` (dev only), bypass and redirect login → dashboard
3. Create Supabase SSR client with cookie handling
4. Call `supabase.auth.getUser()`
5. If protected route + no user → redirect to `/login?next=/dashboard`
6. Otherwise, allow request

**Security:**
- Production blocks `SKIP_AUTH_MIDDLEWARE=true` at startup
- Supabase SSR client handles session cookies
- OAuth flow (if enabled) delegates to Supabase Auth

---

## Blockers

### 1. Missing node_modules ✅ RESOLVED

**Previous Error:** `Cannot find package 'zod' imported from apps/web/lib/env.ts`

**Resolution Applied:**
```powershell
cd apps/web
pnpm install
```

**Validation Results:**
```
✔ tests 14 | pass 14 | fail 0 | duration 1461ms
```

All 14 API URL configuration precedence tests now passing. This also unblocks Task 3 TypeScript tooling (lint, typecheck, format).

### 2. Docker Daemon Not Running ❌ PENDING

**Error:** `failed to connect to docker API at npipe:////./pipe/dockerDesktopLinuxEngine`

**Impact:**
- Cannot start web/API/ingress services
- Cannot run Playwright end-to-end tests
- Cannot verify runtime proxy behavior
- Affects Tasks 4 (data foundations), 5 (Temporal/orchestration), and 6 (web validation)

**Resolution:**
1. Start Docker Desktop on Windows
2. Verify: `docker ps`

---

## Files Created

| File | Purpose | Status |
|------|---------|--------|
| `TASK6-WEB-INGRESS-VALIDATION.md` | Comprehensive analysis and validation guide | ✅ Complete |
| `apps/web/tests/api-url-config-precedence.test.mjs` | Table-driven configuration tests (14 cases) | ✅ Complete and passing (14/14) |
| `scripts/validate-web-ingress.sh` | 5-phase validation script | ✅ Created, ready for Docker |
| `TASK6-COMPLETION-SUMMARY.md` | This document | ✅ Complete |

---

## Files Analyzed

| File | Key Findings |
|------|--------------|
| `apps/web/lib/env.ts` | Canonical precedence (`??`), production validation, browser proxy resolution |
| `apps/web/next.config.ts` | Same-origin rewrites, CSP/security headers, fallback defaults |
| `apps/web/proxy.ts` | Protected routes, Supabase SSR auth, dev bypass guard |
| `apps/web/playwright.config.mjs` | E2E test configuration, uses `NEXT_PUBLIC_API_BASE_URL` |
| `infra/Caddyfile` | Simple reverse proxy routing |
| `infra/Caddyfile.local-audit` | TLS internal, loopback-bound ingress |
| `infra/docker-compose.gpu.yml` | Production uses canonical `NEXT_PUBLIC_API_BASE_URL` |
| `apps/web/.env.example` | Documents legacy alias for Railway/Render |

---

## Test Coverage

### Static Configuration Tests (Created)

**File:** `apps/web/tests/api-url-config-precedence.test.mjs`

- ✅ 14 test cases covering all precedence scenarios
- ⏸️ Blocked by missing `node_modules` (zod package)
- 🎯 Expected: 14/14 passing after `pnpm install`

### Existing Auth Tests

**File:** `apps/web/tests/config.test.mjs`

- ✅ 4 tests for `SKIP_AUTH_MIDDLEWARE` production rejection
- ⏸️ Also blocked by missing `node_modules`
- 🎯 Expected: 4/4 passing

### Playwright E2E Tests (Existing)

**Files:**
- `apps/web/tests/e2e/login-demo.spec.mjs`
- `apps/web/tests/e2e/creator-journey.spec.mjs`
- `apps/web/tests/e2e/workflow-ui.spec.mjs`
- `apps/web/tests/e2e/browser-performance.spec.mjs`

- ⏸️ Blocked by Docker + node_modules
- 🎯 Expected: Login, workspace creation, project access validated

---

## Validation Checklist

### Static Analysis ✅

- [x] API URL precedence analyzed (canonical > legacy)
- [x] Table-driven tests created (14 cases)
- [x] Browser proxy resolution documented (`/api`, `/supabase`)
- [x] Caddy routing mapped (reverse proxy to web:3000, api:8000, supabase-gateway:8000)
- [x] Security headers documented (CSP, X-Frame-Options, Permissions-Policy)
- [x] Authentication middleware flow mapped (protected routes, Supabase SSR)
- [x] Production validation rules documented (reject localhost/127/invalid)
- [x] Development bypass guard analyzed (`SKIP_AUTH_MIDDLEWARE` blocked in prod)

### Runtime Validation (Blocked)

- [ ] Install node_modules: `pnpm install`
- [ ] Run static config tests: `node --test tests/api-url-config-precedence.test.mjs`
- [ ] Start Docker: `docker compose up web api ingress`
- [ ] Verify Caddy routing: `curl https://app.audit.localhost/`
- [ ] Verify `/api` proxy: Browser request to `/api/health`
- [ ] Verify `/supabase` proxy: Browser request to `/supabase/auth/v1/health`
- [ ] Run Playwright tests: `pnpm playwright test`
- [ ] Verify auth redirect: Unauthenticated `/dashboard` → `/login`
- [ ] Verify CSP enforcement: Browser console (no violations)

---

## Next Steps

### Immediate (Resolve Blockers)

1. **Install Dependencies:**
   ```powershell
   cd d:\Motionly-claude\motionly-main\Motionly-main\apps\web
   pnpm install
   ```

2. **Start Docker Desktop:**
   - Launch Docker Desktop application
   - Wait for daemon to start
   - Verify: `docker ps`

### After Blockers Resolved

3. **Run Static Configuration Tests:**
   ```powershell
   cd apps/web
   node --test tests/api-url-config-precedence.test.mjs
   ```
   - Expected: 14/14 tests passing
   - Validates canonical precedence, production restrictions, browser proxy

4. **Run Full Validation Script:**
   ```bash
   bash scripts/validate-web-ingress.sh
   ```
   - Starts services (web, API, ingress)
   - Validates routing and proxy behavior
   - Runs Playwright end-to-end tests

5. **Document Results:**
   - Update `TASK6-WEB-INGRESS-VALIDATION.md` with test outcomes
   - Add any discovered defects to defect ledger (DEF-xxx)
   - Create regression tests for any failures

---

## Decision Log

### Decision 1: Preserve Both Canonical and Legacy Variables

**Context:** `NEXT_PUBLIC_API_BASE_URL` (canonical) vs `NEXT_PUBLIC_API_URL` (legacy)

**Decision:** Keep both until separately approved removal release

**Rationale:**
- Spec explicitly requires: "Preserve the canonical NEXT_PUBLIC_API_BASE_URL plus legacy NEXT_PUBLIC_API_URL alias until a separately approved removal release"
- Production uses canonical only (`infra/docker-compose.gpu.yml`)
- Legacy provides Railway/Render dashboard compatibility
- Precedence is explicit and tested (canonical via `??` operator)

**Implementation:**
- Canonical takes precedence at `apps/web/lib/env.ts:83`
- Both documented in `.env.example` with clear alias note
- Table-driven tests verify all scenarios (canonical-only, legacy-only, both-present)

### Decision 2: Browser Uses Same-Origin Proxy, Server Uses Actual URLs

**Context:** Client vs server-side API URL resolution

**Decision:** Browser resolves to `/api` (same-origin), server uses actual backend URL

**Rationale:**
- Avoids CORS preflight requests (performance + security)
- Simplifies CSP (`'self'` directive covers proxy paths)
- Credentials automatically included (no explicit CORS headers)
- Server-side fetching needs actual URL for direct backend access

**Implementation:**
- `isClient` check at `apps/web/lib/env.ts:82-83`
- Next.js rewrites at `apps/web/next.config.ts:108-117`
- Supabase uses same pattern (`/supabase` → actual Supabase URL)

### Decision 3: Table-Driven Tests Over Manual Scenarios

**Context:** How to test configuration precedence

**Decision:** Create table-driven test suite with 14 scenarios

**Rationale:**
- Comprehensive coverage (canonical, legacy, both, missing, production/dev)
- Single test file, easy to maintain
- Explicit validation of edge cases (localhost rejection, build phase fallback)
- Future-proof for additional scenarios

**Implementation:**
- `apps/web/tests/api-url-config-precedence.test.mjs`
- Uses `node:test` (built-in test runner)
- Spawns child processes with different env configurations
- Verifies both success cases and error messages

---

## References

### Documentation

- `TASK6-WEB-INGRESS-VALIDATION.md` - Comprehensive analysis
- `TASK6-COMPLETION-SUMMARY.md` - This document
- `README.md` - Project overview (existing)

### Code

- `apps/web/lib/env.ts` - Environment validation and API URL precedence
- `apps/web/next.config.ts` - Next.js configuration, rewrites, security headers
- `apps/web/proxy.ts` - Authentication middleware
- `infra/Caddyfile` - Ingress routing (development)
- `infra/Caddyfile.local-audit` - Ingress routing (local audit mode)
- `infra/docker-compose.gpu.yml` - Production deployment

### Tests

- `apps/web/tests/api-url-config-precedence.test.mjs` - Configuration precedence (14 cases)
- `apps/web/tests/config.test.mjs` - Auth bypass validation (4 cases)
- `apps/web/tests/e2e/*.spec.mjs` - Playwright end-to-end tests

### Scripts

- `scripts/validate-web-ingress.sh` - 5-phase validation script

---

## Task 6 Status

✅ **Static Analysis:** Complete  
⏸️ **Runtime Validation:** Blocked (node_modules + Docker)  
📋 **Deliverables:** All created  
🎯 **Next:** Resolve blockers, run tests, document results

**Completion Criteria:**
- [ ] Dependencies installed (`pnpm install`)
- [ ] Docker running
- [ ] Static tests passing (14/14)
- [ ] Services healthy (web, API, ingress)
- [ ] Proxy routing verified (`/api`, `/supabase`, Caddy)
- [ ] Playwright tests passing (login, workspace, project)
- [ ] Results documented in validation report

**Demo Goal:** A browser can authenticate locally, create/access a workspace/project, and invoke API/Supabase calls through the intended proxy paths.

---

**Task 6 Complete (Static Phase)** | August 30, 2026
