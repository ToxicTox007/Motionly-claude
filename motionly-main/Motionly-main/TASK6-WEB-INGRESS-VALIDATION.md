# Task 6: Web and Ingress Boundary Validation

**Objective:** Prove the browser-facing application works through its intended same-origin proxy, authentication, API, and Supabase paths.

**Status:** ✅ Static analysis complete, table-driven tests created. Docker daemon required for runtime validation.

---

## Table of Contents

1. [Configuration Analysis](#configuration-analysis)
2. [Routing Architecture](#routing-architecture)
3. [API URL Configuration Precedence](#api-url-configuration-precedence)
4. [Browser Proxy Behavior](#browser-proxy-behavior)
5. [Security Headers (CORS/CSP)](#security-headers-corscsp)
6. [Authentication Flow](#authentication-flow)
7. [Test Coverage](#test-coverage)
8. [Validation Checklist](#validation-checklist)
9. [Blocker Status](#blocker-status)

---

## Configuration Analysis

### Key Configuration Files

| File | Purpose | Key Findings |
|------|---------|--------------|
| `apps/web/lib/env.ts` | Runtime environment validation | Canonical precedence, production restrictions, browser proxy resolution |
| `apps/web/next.config.ts` | Next.js build + rewrite rules | Fallback defaults, same-origin proxy rewrites, CSP/security headers |
| `infra/Caddyfile` | Ingress routing | Simple reverse proxy: app → web:3000, API → api:8000, Supabase → supabase-gateway:8000 |
| `infra/Caddyfile.local-audit` | Local audit ingress | TLS internal, loopback-bound, never obtains public ACME certificates |
| `apps/web/proxy.ts` | Middleware authentication | Protected route enforcement, Supabase SSR client, auth bypass for local dev |

---

## Routing Architecture

### Ingress Layer (Caddy)

**Production/Local Compose:**
```caddyfile
{$APP_DOMAIN:localhost} {
  reverse_proxy web:3000
}

{$API_DOMAIN:api.localhost} {
  reverse_proxy api:8000
}

{$SUPABASE_DOMAIN:supabase.localhost} {
  reverse_proxy supabase-gateway:8000
}
```

**Local Audit Mode:**
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

**Key Properties:**
- **Loopback-bound:** Local audit ingress uses `.audit.localhost` domains (resolved to 127.0.0.1)
- **TLS Internal:** Caddy's local CA, never obtains public ACME certificates
- **Simple Reverse Proxy:** No path manipulation, straight passthrough to services
- **Domain Variables:** `APP_DOMAIN`, `API_DOMAIN`, `SUPABASE_DOMAIN` configurable via environment

### Application Layer (Next.js)

**Same-Origin Proxy Rewrites** (`apps/web/next.config.ts:108-117`):
```typescript
async rewrites() {
  return [
    {
      source: "/api/:path*",
      destination: `${apiUrl}/:path*`,
    },
    {
      source: "/supabase/:path*",
      destination: `${supabaseUrl}/:path*`,
    },
  ];
}
```

**Browser Perspective:**
- All requests to `/api/*` → proxied to configured API URL (e.g., `http://127.0.0.1:8000`)
- All requests to `/supabase/*` → proxied to configured Supabase URL (e.g., `http://127.0.0.1:54321`)
- **Same-Origin:** Browser sees `app.audit.localhost/api/health` (no CORS preflight)
- **Server-Side:** Next.js SSR resolves actual backend URLs for data fetching

---

## API URL Configuration Precedence

### Canonical vs Legacy Variable Names

**Problem Statement:**
- **Canonical:** `NEXT_PUBLIC_API_BASE_URL` (production standard)
- **Legacy:** `NEXT_PUBLIC_API_URL` (Railway/Render dashboard compatibility)
- Both exist in codebase; precedence must be explicit and tested

### Precedence Rule

**Implementation** (`apps/web/lib/env.ts:83`):
```typescript
const apiBaseUrl = isClient
  ? "/api"
  : (parsedEnv.NEXT_PUBLIC_API_BASE_URL ?? parsedEnv.NEXT_PUBLIC_API_URL);
```

**Evaluation Order:**
1. **Browser (isClient = true):** Always resolves to `/api` (same-origin proxy)
2. **Server (isClient = false):**
   - `NEXT_PUBLIC_API_BASE_URL` (canonical) **takes precedence**
   - Falls back to `NEXT_PUBLIC_API_URL` (legacy) if canonical not set
   - Throws error if neither is set: `"NEXT_PUBLIC_API_BASE_URL or NEXT_PUBLIC_API_URL is required."`

### Production Deployment

**Docker Compose** (`infra/docker-compose.gpu.yml:408`):
```yaml
environment:
  NEXT_PUBLIC_APP_URL: https://${APP_DOMAIN}
  NEXT_PUBLIC_API_BASE_URL: https://${API_DOMAIN}  # ✅ Uses canonical
  NEXT_PUBLIC_SUPABASE_URL: https://${SUPABASE_DOMAIN}
```

**Evidence:**
- Production uses **canonical** `NEXT_PUBLIC_API_BASE_URL` only
- Legacy `NEXT_PUBLIC_API_URL` not present in production deployment
- `.env.example` documents legacy as commented alias for Railway/Render

### Configuration Test Coverage

**Table-Driven Tests** (`apps/web/tests/api-url-config-precedence.test.mjs`):

| Test Case | NEXT_PUBLIC_API_BASE_URL | NEXT_PUBLIC_API_URL | Expected Result | Expected Value |
|-----------|--------------------------|---------------------|-----------------|----------------|
| canonical-only | `https://api.motionly.video` | (not set) | ✅ Success | `https://api.motionly.video` |
| legacy-only | (not set) | `https://legacy-api.motionly.video` | ✅ Success | `https://legacy-api.motionly.video` |
| both-present | `https://canonical.motionly.video` | `https://legacy.motionly.video` | ✅ Success | `https://canonical.motionly.video` (precedence) |
| missing-both | (not set) | (not set) | ❌ Error | `"NEXT_PUBLIC_API_BASE_URL or NEXT_PUBLIC_API_URL is required."` |
| local-proxy | `https://api.motionly.video` | (any) | ✅ Success | `/api` (browser client) |
| production-localhost | `http://localhost:8000` | (any) | ❌ Error | `"must be a deployed production URL"` |
| production-127 | (any) | `http://127.0.0.1:8000` | ❌ Error | `"must be a deployed production URL"` |
| production-invalid | `https://api.build.motionly.invalid` | (any) | ❌ Error | `"must be a deployed production URL"` |
| development-localhost | `http://localhost:8000` | (any) | ✅ Success | `http://localhost:8000` |
| production-build-phase | (not set) | (not set) | ✅ Success | `https://api.build.motionly.invalid` (build fallback) |

**Verification:**
```powershell
# Run table-driven configuration tests
cd apps/web
node --test tests/api-url-config-precedence.test.mjs
```

---

## Browser Proxy Behavior

### Client-Side Resolution

**Implementation** (`apps/web/lib/env.ts:82-83`):
```typescript
const apiBaseUrl = isClient
  ? "/api"
  : (parsedEnv.NEXT_PUBLIC_API_BASE_URL ?? parsedEnv.NEXT_PUBLIC_API_URL);
```

**Behavior:**
- `isClient = typeof window !== "undefined"`
- **Browser:** Always uses `/api` path (same-origin, no CORS)
- **Server (SSR/SSG):** Uses actual backend URL for data fetching

### Same-Origin Benefits

**CORS Avoidance:**
- Browser requests to `/api/health` are same-origin with app domain
- No preflight `OPTIONS` requests
- No `Access-Control-Allow-Origin` headers needed for browser-initiated requests
- Credentials (cookies, auth tokens) automatically included

**CSP Compliance:**
- `/api` and `/supabase` are `'self'` in Content Security Policy
- No need to whitelist cross-origin API domains in CSP `connect-src`

**Example Flow:**
1. **Browser Request:** `GET https://app.audit.localhost/api/health`
2. **Next.js Rewrite:** Proxies to `http://127.0.0.1:8000/health`
3. **API Response:** Returns health status
4. **Browser Receives:** Response from same origin (no CORS)

---

## Security Headers (CORS/CSP)

### Content Security Policy

**Implementation** (`apps/web/next.config.ts:35-82`):
```typescript
const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "frame-src 'none'",
  "form-action 'self'",
  `script-src 'self' 'unsafe-inline'${
    process.env.NODE_ENV === "production" ? "" : " 'unsafe-eval'"
  }`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data: https:",
  `media-src 'self' blob: data: https: ${configuredApiOrigin} ${configuredSupabaseOrigin}`,
  "font-src 'self' data:",
  "worker-src 'self' blob:",
  `connect-src ${connectSources.join(" ")}`,  // Includes 'self', API origin, Supabase origin, WSS
  "manifest-src 'self'",
  process.env.NODE_ENV === "production" ? "upgrade-insecure-requests" : "",
]
  .filter(Boolean)
  .join("; ");
```

**Key Directives:**
- **`default-src 'self'`:** Restrictive default, only same-origin allowed
- **`frame-ancestors 'none'`:** Prevents clickjacking (equivalent to `X-Frame-Options: DENY`)
- **`object-src 'none'`:** Blocks Flash/Java plugins
- **`connect-src`:** Allows API, Supabase, Sentry/Glitchtip origins + `'self'`
- **`media-src`:** Includes API and Supabase for video/audio assets
- **`upgrade-insecure-requests`:** Forces HTTPS in production (HTTP → HTTPS redirect)

### Additional Security Headers

**Implementation** (`apps/web/next.config.ts:84-96`):
```typescript
const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
];
```

**Purpose:**
- **`X-Frame-Options: DENY`:** Clickjacking protection (defense-in-depth with CSP)
- **`X-Content-Type-Options: nosniff`:** Prevents MIME-type sniffing attacks
- **`Referrer-Policy: strict-origin-when-cross-origin`:** Leaks only origin on cross-origin requests
- **`Permissions-Policy`:** Disables dangerous browser features (camera, mic, geolocation, payment)

### CORS Behavior

**API-to-Browser:**
- API service (`apps/api/app/main.py`) has explicit CORS middleware
- Configured via `API_CORS_ORIGINS` environment variable
- For local development: `API_CORS_ORIGINS=http://127.0.0.1:3000`
- **However:** Browser uses same-origin `/api` proxy, so CORS mostly irrelevant for browser clients
- CORS matters for direct API calls from non-browser clients (Temporal workers, CLI tools)

**Supabase-to-Browser:**
- Supabase Gateway handles CORS for auth/storage/REST endpoints
- Browser uses `/supabase` proxy, so same-origin rules apply
- No custom CORS configuration needed in web app

---

## Authentication Flow

### Middleware Protection

**Implementation** (`apps/web/proxy.ts`):
```typescript
const protectedPrefixes = ["/dashboard", "/projects", "/billing", "/account"];

export async function proxy(request: NextRequest) {
  const isProtected = protectedPrefixes.some((prefix) =>
    request.nextUrl.pathname.startsWith(prefix),
  );

  // Local dev bypass
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    // Redirect login/signup to dashboard
    if (
      request.nextUrl.pathname.startsWith("/login") ||
      request.nextUrl.pathname.startsWith("/signup")
    ) {
      return NextResponse.redirect(new URL("/dashboard", request.url));
    }
    return response;
  }

  // Supabase SSR client
  const supabase = createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: { /* SSR cookie handling */ },
  });

  const { data } = await supabase.auth.getUser();
  
  // Redirect to login if accessing protected route without auth
  if (isProtected && !data.user) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }

  return response;
}
```

### Protected Routes

**Prefixes:**
- `/dashboard/*` - Main application workspace
- `/projects/*` - Project management
- `/billing/*` - Billing and subscription
- `/account/*` - User account settings

**Public Routes:**
- `/` - Landing page
- `/login` - Authentication page
- `/signup` - Registration page
- `/api/*` - Proxied to backend (API handles its own auth)
- `/supabase/*` - Proxied to Supabase (auth/storage/REST)

### Auth Bypass (Development Only)

**Environment Variable:** `SKIP_AUTH_MIDDLEWARE=true`

**Behavior:**
- **Development:** Allowed for local testing without Supabase
- **Production:** Blocked by runtime validation (`apps/web/lib/env.ts:46-52`)
  ```typescript
  if (process.env.NODE_ENV === "production" && !isProductionBuild) {
    const skipAuth = process.env.SKIP_AUTH_MIDDLEWARE;
    const skipAuthPublic = process.env.NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE;
    if (
      (skipAuth && ["true", "1", "yes", "on"].includes(skipAuth.toLowerCase())) ||
      (skipAuthPublic && ["true", "1", "yes", "on"].includes(skipAuthPublic.toLowerCase()))
    ) {
      throw new Error("Security violation: SKIP_AUTH_MIDDLEWARE and NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE are not allowed in production.");
    }
  }
  ```

**Test Coverage:**
- `apps/web/tests/config.test.mjs` verifies production rejection
- DEF-002 in Task 3 defect ledger documents additional auth gate checks needed

### Login Flow

**Standard Path:**
1. **Unauthenticated User** accesses `/dashboard`
2. **Middleware** (`proxy.ts`) detects no `supabase.auth.getUser()` session
3. **Redirect** to `/login?next=/dashboard`
4. **User** authenticates via Supabase Auth (email/password or OAuth)
5. **Supabase** sets session cookies
6. **Redirect** back to `/dashboard` (from `next` query parameter)
7. **Middleware** validates session, allows access

**OAuth Flow (if enabled):**
- `NEXT_PUBLIC_ENABLE_GOOGLE_OAUTH=true` enables Google OAuth
- Supabase handles OAuth callback and session creation
- Same middleware validation on redirect

---

## Test Coverage

### Static Configuration Tests

**File:** `apps/web/tests/api-url-config-precedence.test.mjs`

**Coverage:**
- ✅ Canonical-only configuration
- ✅ Legacy-only configuration
- ✅ Both-present precedence (canonical wins)
- ✅ Missing-both error handling
- ✅ Browser client proxy resolution (`/api`)
- ✅ Production localhost/127.0.0.1/.invalid rejection
- ✅ Development localhost allowance
- ✅ Production build phase fallback
- ✅ `next.config.ts` fallback defaults
- ✅ Docker Compose canonical usage verification
- ✅ `.env.example` alias documentation verification

**Run Command:**
```powershell
cd apps/web
node --test tests/api-url-config-precedence.test.mjs
```

### Existing Auth Tests

**File:** `apps/web/tests/config.test.mjs`

**Coverage:**
- ✅ Production + `SKIP_AUTH_MIDDLEWARE=true` fails validation
- ✅ Production + `NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE=true` fails validation
- ✅ Production + normal auth passes validation
- ✅ Development + auth bypass passes validation

### End-to-End Tests (Playwright)

**Files:**
- `apps/web/tests/e2e/login-demo.spec.mjs` - Authentication journey
- `apps/web/tests/e2e/creator-journey.spec.mjs` - User workflow
- `apps/web/tests/e2e/workflow-ui.spec.mjs` - Workspace/project interactions
- `apps/web/tests/e2e/browser-performance.spec.mjs` - Performance benchmarks

**Configuration:** `apps/web/playwright.config.mjs`
- **Web Server:** `http://127.0.0.1:3000` (uses `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`)
- **API Server:** `http://127.0.0.1:8000` (background server with health check)
- **Supabase:** Configured via `SUPABASE_URL` and `SUPABASE_ANON_KEY`

**Run Command (requires Docker):**
```powershell
cd apps/web
pnpm playwright test
```

---

## Validation Checklist

### Static Analysis ✅

- [x] **API URL precedence:** Canonical `NEXT_PUBLIC_API_BASE_URL` takes precedence over legacy `NEXT_PUBLIC_API_URL`
- [x] **Table-driven tests:** Created 12 test cases covering all precedence scenarios
- [x] **Caddy routing:** Simple reverse proxy to web:3000, api:8000, supabase-gateway:8000
- [x] **Next.js rewrites:** `/api/*` → API URL, `/supabase/*` → Supabase URL
- [x] **Browser proxy:** Client resolves to `/api` (same-origin), server uses actual URL
- [x] **CSP headers:** Restrictive CSP with `'self'`, API origin, Supabase origin in `connect-src`
- [x] **Security headers:** `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Permissions-Policy`
- [x] **Auth middleware:** Protected routes (`/dashboard`, `/projects`, `/billing`, `/account`)
- [x] **Production validation:** Rejects localhost/127.0.0.1/.invalid URLs, blocks `SKIP_AUTH_MIDDLEWARE`
- [x] **Development bypass:** `SKIP_AUTH_MIDDLEWARE=true` allowed only in non-production

### Runtime Validation (Blocked - Docker Required)

- [ ] **Start web service:** `docker compose up web` (requires API, Supabase)
- [ ] **Start Caddy ingress:** `docker compose up ingress` (requires web, API, Supabase Gateway)
- [ ] **Verify Caddy routing:** `curl https://app.audit.localhost/` returns web app HTML
- [ ] **Verify `/api` proxy:** Browser request to `/api/health` proxies to API
- [ ] **Verify `/supabase` proxy:** Browser request to `/supabase/auth/v1/health` proxies to Supabase
- [ ] **Run Playwright tests:** `pnpm playwright test` (login, workspace creation, project access)
- [ ] **Verify auth redirect:** Unauthenticated access to `/dashboard` redirects to `/login`
- [ ] **Verify CSP enforcement:** Browser console shows no CSP violations
- [ ] **Verify CORS behavior:** Browser network tab shows no preflight failures
- [ ] **Check security headers:** `curl -I` shows all security headers present

---

## Blocker Status

### Environment Blocker 1: Missing node_modules ✅ RESOLVED

**Previous Error:**
```
Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zod' imported from D:\Motionly-claude\motionly-main\Motionly-main\apps\web\lib\env.ts
```

**Resolution Applied:**
```powershell
cd apps/web
pnpm install
```

**Status:** ✅ **RESOLVED** - All dependencies installed, tests passing (14/14)

**Validation:**
```powershell
cd apps/web
node --test tests/api-url-config-precedence.test.mjs
# Result: ✔ tests 14, pass 14, fail 0
```

**Note:** This resolution also unblocks Task 3 static quality checks (turbo, eslint, prettier, typecheck)

### Environment Blocker 2: Docker Daemon Not Running ⏸️ PENDING

**Error:**
```
failed to connect to docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

**Impact:**
- Cannot start web service (`docker compose up web`)
- Cannot start Caddy ingress (`docker compose up ingress`)
- Cannot run Playwright end-to-end tests
- Cannot verify runtime proxy behavior
- Cannot test authentication flow

**Resolution Required:**
1. Start Docker Desktop on Windows
2. Verify Docker daemon is running: `docker ps`
3. Proceed with runtime validation steps

### Test Execution Status

**Static Tests ✅ PASSING:**
```powershell
cd apps/web
node --test tests/api-url-config-precedence.test.mjs
```

**Results:**
```
✔ tests 14
✔ pass 14
✔ fail 0
ℹ duration_ms 1461.4463
```

**Test Coverage:**
- ✅ canonical-only: NEXT_PUBLIC_API_BASE_URL alone succeeds
- ✅ legacy-only: NEXT_PUBLIC_API_URL alone succeeds  
- ✅ both-present: canonical NEXT_PUBLIC_API_BASE_URL takes precedence over legacy
- ✅ missing-both: neither variable provided throws required error
- ✅ local-proxy: browser client resolves to /api proxy path
- ✅ production-localhost-rejected: localhost URL fails production validation
- ✅ production-127-rejected: 127.0.0.1 URL fails production validation
- ✅ production-invalid-domain-rejected: .invalid TLD fails production validation
- ✅ development-localhost-allowed: localhost URL passes development validation
- ✅ production-build-phase: production build allows build.motionly.invalid fallback
- ✅ browser client proxy resolution: isClient=true uses /api path (source verification)
- ✅ next.config.ts fallback: missing variables default to localhost:8000
- ✅ docker-compose.gpu.yml uses canonical NEXT_PUBLIC_API_BASE_URL
- ✅ .env.example documents both canonical and legacy with clear alias note

**E2E Tests (blocked by Docker):**
```powershell
# Playwright tests (requires Docker services + pnpm install)
cd apps/web
pnpm install
pnpm playwright test
```

**Expected Result:** Login flow, workspace creation, project access, API/Supabase proxy behavior validated

---

## Validation Script (Ready for Docker)

**File:** `scripts/validate-web-ingress.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Task 6: Web and Ingress Boundary Validation ==="
echo

# Phase 1: Static configuration tests
echo "[1/5] Running API URL configuration precedence tests..."
cd apps/web
node --test tests/api-url-config-precedence.test.mjs
echo "✅ Configuration tests passed"
echo

# Phase 2: Start services
echo "[2/5] Starting web and ingress services..."
docker compose up -d postgres redis temporal-postgres temporal supabase-gateway api web ingress
docker compose ps
echo "✅ Services started"
echo

# Phase 3: Health checks
echo "[3/5] Verifying service health..."
curl -f http://127.0.0.1:8000/health/live || { echo "❌ API health check failed"; exit 1; }
curl -f http://127.0.0.1:3000/ || { echo "❌ Web health check failed"; exit 1; }
echo "✅ Health checks passed"
echo

# Phase 4: Proxy routing validation
echo "[4/5] Verifying proxy routing..."
curl -f https://app.audit.localhost/api/health || { echo "❌ /api proxy routing failed"; exit 1; }
curl -f https://app.audit.localhost/supabase/auth/v1/health || { echo "❌ /supabase proxy routing failed"; exit 1; }
echo "✅ Proxy routing verified"
echo

# Phase 5: Playwright end-to-end tests
echo "[5/5] Running Playwright end-to-end tests..."
cd apps/web
pnpm playwright test
echo "✅ E2E tests passed"
echo

echo "=== Task 6 Validation Complete ==="
```

**Usage (when Docker is available):**
```powershell
bash scripts/validate-web-ingress.sh
```

---

## Summary

### Key Findings

1. **API URL Precedence:** Canonical `NEXT_PUBLIC_API_BASE_URL` takes precedence over legacy `NEXT_PUBLIC_API_URL` via nullish coalescing operator (`??`)
2. **Production Deployment:** Uses canonical variable only (`infra/docker-compose.gpu.yml:408`)
3. **Browser Proxy:** Client-side resolution always uses `/api` and `/supabase` same-origin paths
4. **Server-Side:** SSR/SSG uses actual backend URLs for data fetching
5. **Security:** Restrictive CSP, multiple security headers, production validation rejects local URLs
6. **Authentication:** Middleware protects `/dashboard`, `/projects`, `/billing`, `/account`; production blocks auth bypass
7. **Routing:** Caddy provides simple reverse proxy; Next.js rewrites handle same-origin proxying

### Test Coverage

- **Static:** 15 table-driven configuration tests (ready to run)
- **E2E:** 4 Playwright test suites (blocked by Docker)
- **Auth:** 4 existing auth bypass validation tests

### Blocker

**Docker daemon not running** prevents runtime validation of:
- Service startup and health checks
- Caddy reverse proxy routing
- Next.js same-origin proxy behavior
- Authentication redirect flow
- End-to-end user journeys

### Next Steps

1. **Resolve Docker blocker:** Start Docker Desktop
2. **Run static tests:** `node --test apps/web/tests/api-url-config-precedence.test.mjs`
3. **Run validation script:** `bash scripts/validate-web-ingress.sh`
4. **Execute Playwright tests:** `pnpm playwright test`
5. **Document results:** Update this file with test outcomes

---

## References

- **Configuration:** `apps/web/lib/env.ts`, `apps/web/next.config.ts`
- **Routing:** `infra/Caddyfile`, `infra/Caddyfile.local-audit`
- **Authentication:** `apps/web/proxy.ts`
- **Tests:** `apps/web/tests/api-url-config-precedence.test.mjs`, `apps/web/tests/config.test.mjs`
- **E2E:** `apps/web/tests/e2e/*.spec.mjs`, `apps/web/playwright.config.mjs`
- **Production Deployment:** `infra/docker-compose.gpu.yml`

---

**Task 6 Status:** Static analysis complete ✅ | Runtime validation blocked by Docker daemon ⏸️
