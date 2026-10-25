import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  modelWarningBadges,
  parseRecordJson,
  routeHealthBadges,
  routeWarningBadges,
  sortRoutesByPriority,
} from "../lib/admin-inference.ts";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(resolve(root, relativePath), "utf8");
}

// ---------------------------------------------------------------------------
// Pure helper: Section 2.7 warning badges, ordering and record parsing.
// ---------------------------------------------------------------------------

test("model badges flag a provider-managed revision, missing licence and non-commercial use", () => {
  const badges = modelWarningBadges({
    id: "m1",
    revision: "provider-managed",
    license_id: "",
    commercial_use: false,
  });
  const codes = badges.map((b) => b.code);
  assert.ok(codes.includes("provider_managed_revision"));
  assert.ok(codes.includes("missing_remote_license"));
  assert.ok(codes.includes("noncommercial_license"));
  // Meaning is never carried by colour alone: every badge has visible text.
  for (const badge of badges) assert.ok(badge.label.length > 0);
});

test("a fully specified commercial model produces no warning badges", () => {
  const badges = modelWarningBadges({
    id: "m2",
    revision: "sha256:abc",
    license_id: "Apache-2.0",
    commercial_use: true,
  });
  assert.deepEqual(badges, []);
});

test("route badges flag unknown pricing, missing hard cap and missing data-processor metadata", () => {
  const badges = routeWarningBadges({
    id: "r1",
    pricing_status: "unknown",
  });
  const codes = badges.map((b) => b.code);
  assert.ok(codes.includes("unknown_pricing"));
  assert.ok(codes.includes("no_hard_cap"));
  assert.ok(codes.includes("missing_data_processor"));
});

test("a priced route with a hard cap and data processor has only expected badges", () => {
  const badges = routeWarningBadges({
    id: "r2",
    pricing_status: "priced",
    hard_cap_max_requests: 100,
    data_processor: "operator",
  });
  assert.deepEqual(badges, []);
});

test("route health badges reflect provider-managed revision and unknown pricing", () => {
  const badges = routeHealthBadges({
    route_id: "r3",
    enabled: true,
    managed: "database",
    circuit_state: "closed",
    total_calls: 0,
    fallback_count: 0,
    model_revision: "provider-managed",
    pricing_status: "unknown",
  });
  const codes = badges.map((b) => b.code);
  assert.ok(codes.includes("provider_managed_revision"));
  assert.ok(codes.includes("unknown_pricing"));
  assert.ok(codes.includes("no_hard_cap"));
});

test("routes sort by ascending priority then id", () => {
  const ordered = sortRoutesByPriority([
    { id: "b", managed: "database", record: { priority: 10 } },
    { id: "a", managed: "database", record: { priority: 5 } },
    { id: "c", managed: "database", record: {} },
  ]);
  assert.deepEqual(
    ordered.map((r) => r.id),
    ["a", "b", "c"],
  );
});

test("parseRecordJson rejects non-objects and records without an id", () => {
  assert.equal(parseRecordJson("").ok, false);
  assert.equal(parseRecordJson("[]").ok, false);
  assert.equal(parseRecordJson("{not json").ok, false);
  assert.equal(parseRecordJson('{"foo":1}').ok, false);
  const good = parseRecordJson('{"id":"r1","capability":"x"}');
  assert.equal(good.ok, true);
  assert.equal(good.ok && good.record.id, "r1");
});

// ---------------------------------------------------------------------------
// Source contract: admin-only, server-side auth, no secret in client state.
// ---------------------------------------------------------------------------

test("the page enforces admin access server-side and never fetches a secret value", async () => {
  const page = await source("app/(app)/admin/ai/page.tsx");
  assert.match(page, /getAdminApiContext/);
  assert.match(page, /redirect\("\/dashboard\?error=admin_access_denied"\)/);
  // The admin inference prefix and the registry/health/secret reads are present.
  assert.match(page, /const PREFIX = "\/admin\/inference"/);
  assert.match(page, /\$\{PREFIX\}\/registry/);
  assert.match(page, /\$\{PREFIX\}\/routes\/health/);
  assert.match(page, /\$\{PREFIX\}\/secrets/);
  // Only secret metadata is passed to the client.
  assert.match(page, /secretList\?\.secrets/);
});

test("mutations are server actions guarded by the admin context", async () => {
  const actions = await source("app/(app)/admin/ai/actions.ts");
  assert.match(actions, /"use server"/);
  assert.match(actions, /getAdminApiContext/);
  assert.match(actions, /Admin access is required/);
  const PREFIX = "/admin/inference";
  assert.ok(actions.includes(`const PREFIX = "${PREFIX}"`));
  // Every mutating verb is present for adapters, models, routes and secrets.
  for (const kind of ["adapters", "models", "routes"]) {
    assert.ok(actions.includes(`upsert("${kind}"`), `actions upsert ${kind}`);
  }
  assert.ok(actions.includes("${PREFIX}/${kind}"));
  assert.ok(actions.includes("${PREFIX}/secrets"));
  assert.match(actions, /routes\/\$\{encodeURIComponent\(routeId\)\}\/test/);
  assert.match(actions, /rotate/);
  // A paid async test requires explicit allow_paid confirmation.
  assert.match(actions, /allow_paid/);
  assert.match(actions, /paid_confirmation_required/);
});

test("the secret value never persists in client state and is write-only in the UI", async () => {
  const client = await source("app/(app)/admin/ai/AdminAiClient.tsx");
  // The value input is a password field submitted once, then cleared.
  assert.match(client, /type="password"/);
  assert.match(client, /setValue\(""\)/);
  assert.match(client, /setNewValue\(""\)/);
  // The value is never written back from any server response.
  assert.doesNotMatch(client, /fingerprint.*value|value.*fingerprint/i);
  // Secrets table shows metadata columns only (no value column).
  assert.match(client, /Fingerprint/);
  assert.match(client, /Key version/);
});

test("the UI has the required panels, keyboard reorder and delete confirmation", async () => {
  const client = await source("app/(app)/admin/ai/AdminAiClient.tsx");
  assert.match(client, /Capability matrix/);
  assert.match(client, /Route health and circuits/);
  assert.match(client, /Route test/);
  assert.match(client, /Route priority and fallback order/);
  // Keyboard-operable reorder: native buttons with aria-labels.
  assert.match(
    client,
    /aria-label=\{`Move \$\{routeId\} earlier in priority`\}/,
  );
  assert.match(client, /aria-label=\{`Move \$\{routeId\} later in priority`\}/);
  // Delete goes through a confirmation alertdialog.
  assert.match(client, /role="alertdialog"/);
  assert.match(client, /Confirm delete/);
  // File entries are shown as managed/read-only.
  assert.match(client, /File · read-only/);
  assert.match(client, /readOnly/);
});

test("warning badges render visible text, not colour alone", async () => {
  const client = await source("app/(app)/admin/ai/AdminAiClient.tsx");
  // The badge component always renders the label text alongside the tone class.
  assert.match(client, /\{badge\.label\}/);
  assert.match(client, /aria-label="Warnings"/);
});

test("the admin AI screens use the neumorphic design system only", () => {
  assertNeumorphic([
    "app/(app)/admin/ai/page.tsx",
    "app/(app)/admin/ai/AdminAiClient.tsx",
  ]);
});
