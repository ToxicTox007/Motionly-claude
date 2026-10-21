// Task 13 / B33 + B36.
// B33: the pricing page reads plans from the API and shows an error state (not
// fabricated prices) when the API is down.
// B36: the files Task 13 touches use the generated API contract for shared
// response types instead of hand-written duplicates.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("pricing page reads plans from the API, not a hand-written fallback", async () => {
  const source = await readFile(new URL("app/pricing/page.tsx", root), "utf8");
  assert.match(source, /getPublicPlans/);
  // The hand-written billing fallback is no longer the source of plans.
  assert.doesNotMatch(source, /fallbackBillingStatus\.plans/);
  // An error state exists for when the API is down (no fake prices).
  assert.match(source, /!plansResult\.ok/);
  assert.match(source, /temporarily unavailable/i);
});

test("getPublicPlans returns an explicit error state and uses generated types", async () => {
  const source = await readFile(new URL("lib/plans.ts", root), "utf8");
  assert.match(source, /PublicPlansResponse/);
  assert.match(source, /from "@motionly\/shared"/);
  assert.match(source, /\/plans/);
  assert.match(source, /\{ ok: false \}/);
});

// B36: the generated contract is the single source of truth for these response
// shapes. Flag any hand-written duplicate `type`/`interface` re-declaration of
// a response type in the files Task 13 owns.
test("touched files do not re-declare generated response types by hand", async () => {
  const touched = [
    "app/(app)/dashboard/page.tsx",
    "app/(app)/projects/page.tsx",
    "app/pricing/page.tsx",
    "lib/plans.ts",
  ];
  // Names that must come from @motionly/shared, never a local duplicate.
  const generatedOnly = [
    "PlanDefinition",
    "PublicPlansResponse",
    "ProjectListResponse",
    "ProjectLatestUpload",
    "StorageQuotaResponse",
  ];
  for (const relative of touched) {
    const source = await readFile(new URL(relative, root), "utf8");
    for (const name of generatedOnly) {
      const localDecl = new RegExp(`(?:interface|type)\\s+${name}\\b`);
      assert.doesNotMatch(
        source,
        localDecl,
        `${relative} hand-declares ${name}; import it from @motionly/shared instead`,
      );
    }
  }
});

test("shared package exposes the generated plan/project response aliases", async () => {
  const source = await readFile(
    new URL("../../../packages/shared/src/index.ts", import.meta.url),
    "utf8",
  );
  for (const name of [
    "PlanDefinition",
    "PublicPlansResponse",
    "ProjectListResponse",
    "ProjectLatestUpload",
    "StorageQuotaResponse",
  ]) {
    const alias = new RegExp(
      `export type ${name} =\\s*ApiComponents\\["schemas"\\]`,
    );
    assert.match(source, alias, `missing generated alias for ${name}`);
  }
});
