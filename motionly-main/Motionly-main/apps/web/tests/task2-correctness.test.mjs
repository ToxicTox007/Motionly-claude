import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("project loading checks every active workspace membership", async () => {
  const source = await readFile(
    new URL("app/(app)/projects/[projectId]/page.tsx", root),
    "utf8",
  );

  assert.match(source, /const \{ data: memberships \} = await supabase/);
  assert.match(source, /return memberships\.map\(\(membership\) =>/);
  assert.match(source, /contexts\.map\(async \(context\) =>/);
  assert.match(source, /projectMatches\.find\(\(candidate\) => candidate\.project !== null\)/);
  assert.doesNotMatch(source, /\.limit\(1\)[\s\S]*\.maybeSingle\(\)/);
});

test("normal AI generation uses caches while explicit regeneration can bypass them", async () => {
  const aiStep = await readFile(new URL("components/editor/ai-step.tsx", root), "utf8");
  const editor = await readFile(new URL("components/editor-client.tsx", root), "utf8");
  const scale = await readFile(new URL("components/editor/ScalePanel.tsx", root), "utf8");
  const broll = await readFile(
    new URL("components/editor/tabs/broll-tab.tsx", root),
    "utf8",
  );

  assert.doesNotMatch(aiStep, /force:\s*true/);
  assert.doesNotMatch(editor, /body: \{ density: "from_plan", force: true \}/);
  assert.doesNotMatch(editor, /rights_confirmed: true,\s*force: true/);
  assert.doesNotMatch(scale, /saved_template_id: template\.id, force: true/);
  assert.doesNotMatch(scale, /style_profile_id: profile\.id, force: true/);
  assert.match(broll, /async function regenerateBroll\(\)[\s\S]*force: true/);
});
