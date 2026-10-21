// Task 12: admin-assisted password reset (web). Source-contract tests like
// signup-without-email.test.mjs: they read the web source and the shared
// helpers to prove the flow wires the right calls and never emails.
import assert from "node:assert/strict";
import test from "node:test";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (path) => readFileSync(resolve(webDir, path), "utf8");

test("the web password policy mirrors the Python source", async () => {
  const { passwordProblem, COMMON_PASSWORDS, PASSWORD_MIN_LENGTH } =
    await import("../lib/password-policy.mjs");

  // Same verdicts as the Python policy's own unit test (test_signup.py).
  assert.equal(
    passwordProblem("correct-horse-battery", "asha@example.com"),
    null,
  );
  assert.equal(passwordProblem("Tr0ub4dor&3", "asha@example.com"), null);
  assert.equal(passwordProblem("short", "asha@example.com"), "too_short");
  assert.equal(passwordProblem("qwertyuiop", "asha@example.com"), "common");
  assert.equal(passwordProblem("PASSWORD1", "asha@example.com"), "common");
  assert.equal(
    passwordProblem("abababababab", "asha@example.com"),
    "too_simple",
  );
  assert.equal(
    passwordProblem("my-asha-password", "asha@example.com"),
    "contains_email",
  );
  assert.equal(passwordProblem("€".repeat(25), "asha@example.com"), "too_long");

  // Without an email, the local-part rule is simply skipped.
  assert.equal(passwordProblem("correct-horse-battery"), null);
  assert.equal(PASSWORD_MIN_LENGTH, 8);

  // The common-password list is identical to the Python frozenset, member for
  // member, so a password refused by one is refused by the other.
  const policy = readFileSync(
    resolve(webDir, "../api/app/services/signup_policy.py"),
    "utf8",
  );
  const pythonBlock = policy.slice(
    policy.indexOf("COMMON_PASSWORDS = frozenset("),
    policy.indexOf("def normalize_email"),
  );
  const pythonWords = new Set(
    [...pythonBlock.matchAll(/"([^"]+)"/g)].map((m) => m[1]),
  );
  assert.deepEqual(
    [...COMMON_PASSWORDS].sort(),
    [...pythonWords].sort(),
    "the JS and Python common-password lists must match exactly",
  );
  assert.equal(policy.includes("PASSWORD_MIN_LENGTH = 8"), true);
});

test("the forgot-password action posts to the API and never leaks existence", () => {
  const actions = read("app/(auth)/actions.ts");
  const fn = actions.slice(
    actions.indexOf("export async function requestPasswordReset"),
    actions.indexOf("export async function resetPassword"),
  );
  assert.match(fn, /`\$\{env\.apiBaseUrl\}\/auth\/password-reset-requests`/);
  // Rate limits key on the viewer, not the web server.
  assert.match(fn, /viewerAddressHeaders\(await headers\(\)\)/);
  // The same confirmation whatever the email: redirect carries no account hint.
  assert.match(fn, /\/forgot-password\?sent=1/);
  assert.doesNotMatch(fn, /auth\.signUp\(/);
  assert.doesNotMatch(fn, /emailRedirectTo/);
});

test("the reset-password action enforces the policy, then signs out other sessions", () => {
  const actions = read("app/(auth)/actions.ts");
  const fn = actions.slice(
    actions.indexOf("export async function resetPassword"),
    actions.length,
  );
  assert.match(fn, /passwordProblem\(password\)/);
  assert.match(fn, /updateUser\(\{ password \}\)/);
  // Every OTHER session is ended, so a reset invalidates stolen logins.
  assert.match(fn, /signOut\(\{ scope: "others" \}\)/);
  // The recovery session is required server-side: no session → clear error,
  // never a 500. The password change only happens when a user is present.
  assert.match(fn, /getUser\(\)/);
  // A missing recovery session is a clear error, never a 500.
  assert.match(fn, /error=no_session/);
});

test("the /auth/confirm page reads the fragment, clears it, and POSTs the token to the server", () => {
  // Kept in app/auth/confirm (outside the (auth) group) so it is public.
  assert.ok(existsSync(resolve(webDir, "app/auth/confirm/page.tsx")));
  const confirm = read("app/auth/confirm/page.tsx");
  // The token rides in the URL fragment, so it never reaches server logs.
  assert.match(confirm, /window\.location\.hash/);
  // The fragment is cleared from the address bar before anything else, so a
  // reload or a copied URL carries no token.
  assert.match(confirm, /history\.replaceState/);
  // The CHECK moves to the server: the page does NOT verify in the browser.
  assert.doesNotMatch(confirm, /verifyOtp/);
  assert.doesNotMatch(confirm, /createClient/);
  // It submits the token to the server route handler in a POST body, not a GET
  // query string (which would reach server logs).
  assert.match(confirm, /\/app-api\/auth\/confirm/);
  assert.match(confirm, /method: "POST"/);
  assert.match(confirm, /JSON\.stringify\(\{ token_hash: tokenHash \}\)/);
  // On success the server set the recovery cookie; continue to reset-password.
  assert.match(confirm, /\/reset-password/);
  // Invalid/expired/reused show a clear error, not a crash.
  assert.match(confirm, /RESET_LINK_ERROR_MESSAGES/);
  // The confirm page reads the fragment on the client; nothing server-side
  // logs a token-bearing query string.
  assert.doesNotMatch(confirm, /searchParams\.get\("token/);
});

test("the /app-api/auth/confirm route verifies the recovery token on the server", () => {
  // The CHECK runs server-side: the route calls verifyOtp through the SERVER
  // Supabase client, so the recovery session cookie is set on the response and
  // the viewer-address header is carried (lib/supabase/server.ts).
  assert.ok(existsSync(resolve(webDir, "app/app-api/auth/confirm/route.ts")));
  const route = read("app/app-api/auth/confirm/route.ts");
  // POST only — a token must never arrive as a GET query string.
  assert.match(route, /export async function POST/);
  assert.doesNotMatch(route, /export async function GET/);
  // Reads the token from the request BODY, not the URL.
  assert.match(route, /token_hash/);
  assert.doesNotMatch(route, /searchParams\.get\("token/);
  // Verifies through the server Supabase client, which sets the cookie.
  assert.match(route, /from "@\/lib\/supabase\/server"/);
  assert.match(route, /verifyOtp\(\{\s*type: "recovery"/);
  assert.match(route, /token_hash/);
  // A missing/invalid/expired/reused token maps to the existing clear reasons,
  // never a 500. The token itself is never logged.
  assert.match(route, /resetLinkErrorReason/);
  assert.doesNotMatch(route, /console\.[a-z]+\([^)]*token_hash/);
  assert.doesNotMatch(route, /console\.[a-z]+\([^)]*tokenHash/);
});

test("the login page links to the forgot-password flow", () => {
  const login = read("app/(auth)/login/page.tsx");
  assert.match(login, /href="\/forgot-password"/);
  assert.match(login, /Forgot password\?/);
});

test("the forgot-password page shows one answer for any email", () => {
  const page = read("app/(auth)/forgot-password/page.tsx");
  assert.match(page, /RESET_REQUEST_CONFIRMATION/);
  assert.match(page, /requestPasswordReset/);
  assert.doesNotMatch(page, /account (does not|doesn't) exist/i);
});

test("the reset link error reasons map GoTrue codes", async () => {
  const { resetLinkErrorReason, RESET_LINK_ERROR_MESSAGES } =
    await import("../lib/password-reset.mjs");
  assert.equal(resetLinkErrorReason("otp_expired"), "expired");
  assert.equal(resetLinkErrorReason("missing_token"), "missing");
  assert.equal(resetLinkErrorReason("something_else"), "invalid");
  assert.equal(resetLinkErrorReason(undefined), "invalid");
  for (const reason of ["expired", "invalid", "missing"]) {
    assert.ok(RESET_LINK_ERROR_MESSAGES[reason]);
  }
});

test("the reset-request error reasons map HTTP status", async () => {
  const { resetRequestErrorReason } = await import("../lib/password-reset.mjs");
  assert.equal(resetRequestErrorReason(429), "rate_limited");
  assert.equal(resetRequestErrorReason(500), "system_error");
  assert.equal(resetRequestErrorReason(502), "system_error");
});
