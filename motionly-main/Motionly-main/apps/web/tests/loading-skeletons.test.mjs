// Dashboard task: route-level loading screens render neumorphic shimmer
// skeletons and keep their AppShell title (navigation tests rely on the shell
// being present during loading). The [projectId] loading screen renders the
// editor shell directly (no AppShell) and uses PageTitle instead.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

const APP_SHELL_LOADING = [
  "app/(app)/dashboard/loading.tsx",
  "app/(app)/projects/loading.tsx",
  "app/(app)/billing/loading.tsx",
  "app/(app)/account/loading.tsx",
  "app/(app)/admin/ops/loading.tsx",
];

const PROJECT_DETAILS_LOADING = "app/(app)/projects/[projectId]/loading.tsx";

// FE-30: routes that used to inherit a parent's unrelated skeleton (or none).
// Rendered behaviour (title, status, hidden shimmer, layout kind) is tested in
// tests/components/route-loading.test.tsx.
const ROUTE_SKELETON_LOADING = [
  "app/(app)/admin/loading.tsx",
  "app/(app)/account/team/loading.tsx",
  "app/(app)/account/api-keys/loading.tsx",
  "app/(app)/account/ai-models/loading.tsx",
  "app/(app)/account/api-reference/loading.tsx",
  "app/(app)/projects/new/loading.tsx",
];

test("every signed-in route group has its own loading screen", async () => {
  for (const route of ROUTE_SKELETON_LOADING) {
    const source = await readFile(new URL(route, root), "utf8");
    assert.match(source, /<AppShell title=/, `${route} keeps its shell title`);
    assert.match(source, /<RouteSkeleton label=/, `${route} announces loading`);
  }
});

test("every loading screen uses neu-shimmer or LoadingPixelSkeleton blocks", async () => {
  for (const route of [...APP_SHELL_LOADING, PROJECT_DETAILS_LOADING]) {
    const source = await readFile(new URL(route, root), "utf8");
    assert.ok(
      /neu-shimmer/.test(source) || /LoadingPixelSkeleton/.test(source),
      `${route} must render shimmer skeleton blocks`,
    );
  }
});

test("the five AppShell loading screens keep their shell title", async () => {
  for (const route of APP_SHELL_LOADING) {
    const source = await readFile(new URL(route, root), "utf8");
    assert.match(
      source,
      /<AppShell title=/,
      `${route} must keep <AppShell title=`,
    );
  }
});

test("the project details loading screen keeps its PageTitle", async () => {
  const source = await readFile(new URL(PROJECT_DETAILS_LOADING, root), "utf8");
  assert.match(source, /<PageTitle title="Project" \/>/);
});
