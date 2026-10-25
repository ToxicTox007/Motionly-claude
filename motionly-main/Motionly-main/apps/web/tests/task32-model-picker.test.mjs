import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// Task 32: the user model-picker UI (plan Section 2.11). Source-contract tests
// in the style of task9b-capability-gating.test.mjs and
// task31-capability-screens.test.mjs — one assertion group per guarantee the
// plan requires: the typed client over the real model-choice routes that never
// throws and carries a neutral unknown state; the accessible per-capability
// picker (display name, description, labels, quota weight) with plan-locked
// options disabled via aria-disabled (focus preserved); the owner/admin
// workspace editor and member read-only view; the locked-workspace read-only
// project picker with a reason; the fallback notice where AI results show a
// model_choice.fallback_reason; and that nothing renders a model id, URL or
// secret, with state never signalled by colour alone.

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(resolve(root, relativePath), "utf8");
}

test("model-options client: typed over the real routes through the existing client, never throws", async () => {
  const lib = await source("lib/model-options.ts");
  // Typed from the generated contract — no hand-rolled shape that could drift.
  assert.match(lib, /ApiResponse<"GET \/model-options">/);
  assert.match(lib, /components\["schemas"\]\["WorkspaceModelSettingUpdate"\]/);
  assert.match(lib, /components\["schemas"\]\["ProjectModelSettingUpdate"\]/);
  // Through the existing editorApiRequest client.
  assert.match(lib, /import \{ editorApiRequest \} from "@\/lib\/client-api"/);
  // The three model-choice route families are called from product source.
  assert.match(lib, /"\/model-options"/);
  assert.match(lib, /"\/workspaces\/current\/model-settings"/);
  assert.match(lib, /`\/projects\/\$\{projectId\}\/model-settings`/);
  // GET options, GET/PUT workspace, GET/PUT/DELETE project.
  assert.match(lib, /method: "PUT"/);
  assert.match(lib, /method: "DELETE"/);
  // Every fetch helper catches and returns a neutral value — never throws.
  assert.match(lib, /return unknownOptionsCatalog\(\)/);
  assert.match(lib, /statusUnknown: true/);
});

test("model-options client carries no URL, model id, endpoint or secret", async () => {
  const lib = await source("lib/model-options.ts");
  // The option id is the opaque registry route id; the only identity shown is
  // the operator display name/description/labels. No provider identity leaks.
  for (const forbidden of [
    "base_url",
    "baseUrl",
    "model_id",
    "modelId",
    "secret",
    "api_key",
    "apiKey",
    "endpoint",
    "provider_model",
  ]) {
    assert.ok(
      !new RegExp(`\\b${forbidden}\\b`, "i").test(lib),
      `model-options must not reference ${forbidden}`,
    );
  }
});

test("quota weight reads as 'uses N× quota' and is hidden at the 1× baseline", async () => {
  const lib = await source("lib/model-options.ts");
  assert.match(lib, /export function quotaWeightText/);
  // Baseline weight shows nothing; other weights read as a multiplier.
  assert.match(lib, /weight === 1\) return null/);
  assert.match(lib, /uses \$\{display\}× quota/);
});

test("a plan-locked option is disabled with 'Requires <plan>' using the required plan", async () => {
  const lib = await source("lib/model-options.ts");
  assert.match(lib, /export function optionDisabledReason/);
  assert.match(lib, /if \(option\.allowed\) return null/);
  // The required plan turns the generic reason into an upgrade prompt.
  assert.match(lib, /Requires \$\{pretty\}/);
});

test("the fallback notice shape is a local type and renders only on a fallback", async () => {
  const lib = await source("lib/model-options.ts");
  // Local ModelChoice type so the notice does not depend on a generated type
  // that may not exist yet while Task 31 lands the field.
  assert.match(lib, /export type ModelChoice = \{/);
  assert.match(lib, /fallback_reason\?: string \| null/);
  assert.match(lib, /export function fallbackNoticeText/);
  assert.match(lib, /if \(!choice\.fallback_reason\) return null/);

  const notice = await source("components/editor/ModelChoiceNotice.tsx");
  // State is carried by text + an icon, never colour alone.
  assert.match(notice, /fallbackNoticeText\(modelChoice\)/);
  assert.match(notice, /if \(!text\) return null/);
  assert.match(notice, /role="status"/);
  assert.match(notice, /aria-hidden="true"/);
  // Neutral copy: it never names a model id or route.
  assert.doesNotMatch(notice, /served_option_id/);
});

test("the hook exposes a reusable catalog + resolver surface and never throws", async () => {
  const hook = await source("hooks/useModelOptions.ts");
  assert.match(hook, /catalog: ModelOptionsCatalog/);
  assert.match(
    hook,
    /resolve: \(capability: string\) => CapabilityOptions \| null/,
  );
  assert.match(hook, /statusUnknown: boolean/);
  assert.match(hook, /refresh: \(\) => Promise<void>/);
  // Loads through the never-throwing fetch helper.
  assert.match(
    hook,
    /fetchModelOptions\(\{ workspaceId, projectId, signal \}\)/,
  );
});

test("the per-capability picker is an accessible radio group showing display name, description, labels and quota", async () => {
  const picker = await source("components/editor/ModelPicker.tsx");
  assert.match(picker, /role="radiogroup"/);
  assert.match(picker, /type="radio"/);
  assert.match(picker, /aria-checked=\{checked\}/);
  // Display data for each option.
  assert.match(picker, /quotaWeightText\(option\.quota_weight\)/);
  assert.match(picker, /labelText\(label\)/);
  assert.match(picker, /\{description\}/);
  // One control per capability, labelled by the capability title.
  assert.match(picker, /aria-label=\{title\}/);
});

test("plan-locked options use aria-disabled (focus preserved), not the disabled attribute, and refuse activation", async () => {
  const picker = await source("components/editor/ModelPicker.tsx");
  // The blocked (plan-locked/unavailable) state is aria-disabled, never the
  // disabled attribute — keyboard focus is preserved.
  assert.match(picker, /const ariaDisabled = blocked/);
  assert.match(picker, /aria-disabled=\{ariaDisabled\}/);
  // The real disabled attribute is only used for a transient (saving) state on
  // an otherwise selectable option.
  assert.match(picker, /disabled=\{disabled && !blocked\}/);
  // A blocked option refuses activation but stays focusable.
  assert.match(picker, /if \(blocked \|\| disabled\) return;/);
  assert.match(picker, /if \(blocked\) event\.preventDefault\(\)/);
  // The blocked reason is announced beside the option.
  assert.match(picker, /\{blockedReason\}/);
});

test("a locked workspace shows the project picker read-only with the reason", async () => {
  const picker = await source("components/editor/ModelPicker.tsx");
  assert.match(picker, /export function ProjectModelPicker/);
  // Locked when the effective source is the workspace lock.
  assert.match(picker, /effective_source === "workspace_locked"/);
  assert.match(picker, /readOnly=\{locked\}/);
  assert.match(picker, /locked this choice/);
  // The project override persists through the project settings route.
  assert.match(picker, /saveProjectModelSetting/);
  assert.match(picker, /clearProjectModelSetting/);
  // The 409/422 API errors surface as neutral copy.
  assert.match(picker, /case "model_choice_locked":/);
  assert.match(picker, /case "model_choice_option_invalid":/);
});

test("the workspace AI-models settings are owner/admin editable, member read-only", async () => {
  const page = await source("app/(app)/account/ai-models/page.tsx");
  // Role-gated with the shared predicate (as team/api-keys do).
  assert.match(page, /canManageWorkspace/);
  assert.match(page, /canManage=\{canManage\}/);
  assert.match(page, /AppShell title="AI models"/);

  const client = await source("app/(app)/account/ai-models/AiModelsClient.tsx");
  // Members (no manage rights) see the effective choice read-only.
  assert.match(client, /if \(!canManage\) \{/);
  assert.match(client, /Current choice:/);
  // Owners/admins edit the default, the allow-list and the lock.
  assert.match(client, /saveWorkspaceModelSetting/);
  assert.match(client, /default_option_id/);
  assert.match(client, /allowed_option_ids/);
  assert.match(client, /Lock this choice/);
  // Neutral unknown state when options cannot be fetched.
  assert.match(client, /Model options are unavailable right now/);
});

test("the project picker and fallback notice are mounted in the editor AI step", async () => {
  const step = await source("components/editor/ai-step.tsx");
  assert.match(step, /import \{ ProjectModelPicker \} from "\.\/ModelPicker"/);
  assert.match(
    step,
    /import \{ ModelChoiceNotice \} from "\.\/ModelChoiceNotice"/,
  );
  assert.match(step, /useModelOptions\(\s*workspaceId,\s*projectId,\s*\)/);
  assert.match(step, /resolveModelOptions\("planning\.chat"\)/);
  assert.match(step, /<ProjectModelPicker/);
  assert.match(step, /<ModelChoiceNotice/);
  // The notice reads the optional model_choice defensively.
  assert.match(step, /model_choice\?: ModelChoice/);
});

test("the AI-models settings section has a nav link consistent with team/api-keys", async () => {
  const shell = await source("components/app-shell.tsx");
  // A settings nav entry and the mobile ThemeMenu entry both link it.
  assert.match(shell, /href: "\/account\/ai-models", label: "AI models"/);
  const matches = shell.match(/\/account\/ai-models/g) ?? [];
  assert.ok(
    matches.length >= 2,
    "AI models should be linked in both the sidebar and the theme menu",
  );
});
