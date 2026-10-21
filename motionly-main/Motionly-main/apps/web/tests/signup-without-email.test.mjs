// Task 11: sign-up without email. The sign-up page creates the account with
// the API's rate-limited POST /auth/signup (an already-confirmed user, created
// through Auth's admin API), then signs the new user in. Auth's own sign-up is
// disabled, and nothing waits for a confirmation email.
import assert from "node:assert/strict";
import test from "node:test";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (path) => readFileSync(resolve(webDir, path), "utf8");

test("API refusals map to sign-up page errors", async () => {
  const { signupErrorReason, SIGNUP_ERROR_MESSAGES } =
    await import("../lib/signup.mjs");
  assert.equal(signupErrorReason(409, "email_taken"), "email_taken");
  assert.equal(signupErrorReason(422, "password_weak"), "password_weak");
  assert.equal(signupErrorReason(422, "email_invalid"), "email_invalid");
  assert.equal(signupErrorReason(422, "name_required"), "name_required");
  assert.equal(signupErrorReason(429, "signup_rate_limited"), "rate_limited");
  assert.equal(signupErrorReason(429, undefined), "rate_limited");
  for (const [status, code] of [
    [422, "request_validation_failed"],
    [500, undefined],
    [502, "signup_unavailable"],
    [503, "signup_unavailable"],
  ]) {
    assert.equal(signupErrorReason(status, code), "system_error");
  }
  for (const reason of [
    "email_taken",
    "password_weak",
    "email_invalid",
    "name_required",
    "rate_limited",
    "system_error",
  ]) {
    assert.ok(SIGNUP_ERROR_MESSAGES[reason], reason);
    assert.doesNotMatch(SIGNUP_ERROR_MESSAGES[reason], /email confirm/i);
  }
  assert.match(SIGNUP_ERROR_MESSAGES.email_taken, /log in/i);
});

test("the sign-up action creates the account through the API, then signs in", () => {
  const actions = read("app/(auth)/actions.ts");
  const signup = actions.slice(
    actions.indexOf("export async function signup"),
    actions.indexOf("export async function logout"),
  );
  assert.match(signup, /`\$\{env\.apiBaseUrl\}\/auth\/signup`/);
  // Rate limits key on the viewer, not on the web server.
  assert.match(signup, /viewerAddressHeaders\(await headers\(\)\)/);
  assert.match(signup, /signInWithPassword\(/);
  assert.match(signup, /signupErrorReason\(/);
  assert.match(signup, /redirect\(redirectTo\)/);
  // Auth's own sign-up endpoint is disabled; nothing emails a link.
  assert.doesNotMatch(actions, /auth\.signUp\(/);
  assert.doesNotMatch(actions, /emailRedirectTo/);
  assert.doesNotMatch(actions, /auth\/callback/);
  assert.doesNotMatch(
    actions,
    /email_not_confirmed|over_email_send_rate_limit/,
  );
});

test("no email-confirmation paths remain", () => {
  assert.equal(existsSync(resolve(webDir, "app/auth/callback")), false);
  const login = read("app/(auth)/login/page.tsx");
  assert.doesNotMatch(
    login,
    /check_email|email_not_confirmed|Confirm your email/,
  );
  const signupPage = read("app/(auth)/signup/page.tsx");
  assert.match(signupPage, /SIGNUP_ERROR_MESSAGES/);
  assert.doesNotMatch(signupPage, /Email confirmation/i);

  const offenders = [];
  const walk = (directory) => {
    for (const name of readdirSync(directory)) {
      const path = join(directory, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.(ts|tsx|mjs)$/.test(name)) {
        const text = readFileSync(path, "utf8");
        if (/exchangeCodeForSession|emailRedirectTo|check_email/.test(text))
          offenders.push(path.slice(webDir.length + 1));
      }
    }
  };
  for (const directory of ["app", "components", "hooks", "lib"])
    walk(join(webDir, directory));
  assert.deepEqual(offenders, []);
});

test("the form's password rule matches the API's minimum", () => {
  const signupPage = read("app/(auth)/signup/page.tsx");
  assert.match(signupPage, /val\.length < 8/);
  const policy = readFileSync(
    resolve(webDir, "../api/app/services/signup_policy.py"),
    "utf8",
  );
  assert.match(policy, /PASSWORD_MIN_LENGTH = 8/);
});
