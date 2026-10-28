// Web unit tests for the API-keys settings UI (Task 24). Static source checks
// (the project's convention for server components) assert the one-time-secret
// flow, owner/admin + plan gating, accessibility affordances, and nav wiring.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function source(path) {
  return readFile(new URL(path, root), "utf8");
}

test("API keys page gates on owner/admin and the api_access plan", async () => {
  const page = await source("app/(app)/account/api-keys/page.tsx");
  assert.match(page, /canManageWorkspace/);
  assert.match(page, /entitlements\.api_access/);
  assert.match(page, /Agency-plan feature/);
  // Members see the owner/admin gate.
  assert.match(page, /Owner or admin only/);
  // The list is loaded server-side (in parallel with the plan) and passed to
  // the client component.
  assert.match(page, /loadKeys\(context\.headers\)/);
  assert.match(page, /<ApiKeysClient initialKeys=\{keys\}/);
});

test("the raw key is shown exactly once with a copy button", async () => {
  const client = await source("app/(app)/account/api-keys/ApiKeysClient.tsx");
  // The secret lives only in component state (never re-fetched/persisted).
  assert.match(client, /useState<string \| null>\(null\)/);
  // Copy, its fallback and the announcement are behaviour-tested in
  // tests/components/copy-field.test.tsx.
  assert.match(client, /testId="api-key-secret"/);
  assert.match(client, /<CopyField/);
  assert.match(client, /only time the full key is shown/i);
  // The reveal is announced to assistive tech.
  assert.match(client, /aria-live="polite"/);
});

test("the key list is accessible (labelled table, revoke labels, errors)", async () => {
  const client = await source("app/(app)/account/api-keys/ApiKeysClient.tsx");
  assert.match(
    client,
    /<caption className="sr-only">Workspace API keys<\/caption>/,
  );
  assert.match(client, /aria-label=\{`Revoke API key \$\{key\.name\}`\}/);
  assert.match(client, /role="alert"/);
  // Only the display prefix + last4 are ever rendered, never a full secret.
  assert.match(client, /\{key\.prefix\}…\{key\.last4\}/);
});

test("server actions enforce owner/admin and only return the secret once", async () => {
  const actions = await source("app/(app)/account/api-keys/actions.ts");
  assert.match(actions, /"use server"/);
  assert.match(actions, /canManageWorkspace\(context\.role\)/);
  assert.match(actions, /workspace_role_forbidden/);
  assert.match(
    actions,
    /\/api-keys\/\$\{encodeURIComponent\(keyId\)\}\/revoke/,
  );
  // The create action surfaces the one-time secret from the API response.
  assert.match(actions, /ok: true; secret: string/);
});

test("the API reference page renders the generated allow-list", async () => {
  const page = await source("app/(app)/account/api-reference/page.tsx");
  assert.match(page, /api-key-reference\.json/);
  assert.match(page, /Authorization/);
  assert.match(page, /Available endpoints/);
  assert.match(page, /<caption className="sr-only">/);
});

test("API keys nav is gated to owners/admins in the app shell", async () => {
  const shell = await source("components/app-shell.tsx");
  assert.match(shell, /href: "\/account\/api-keys", label: "API Keys"/);
  assert.match(shell, /showApiKeys/);
  assert.match(shell, /canManageWorkspace\(role\)/);
});
