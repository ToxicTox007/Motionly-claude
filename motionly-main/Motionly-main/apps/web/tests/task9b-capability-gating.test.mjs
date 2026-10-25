import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// Task 9B: the editor gates every optional-capability control on the public
// /capabilities contract. These are source-contract tests in the style of
// task31-capability-screens.test.mjs — one assertion group per guarantee the
// plan requires: the typed client, cache-by-registry-version, the neutral
// unknown state, the three control states (missing/degraded/available), the
// accessibility attributes, and that the client capability type carries no
// URL, model id or secret.

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(resolve(root, relativePath), "utf8");
}

const UNAVAILABLE_TEXT = "Unavailable — not configured by your operator";
const DEGRADED_TEXT = "Limited — running in degraded mode";

test("capabilities client: typed GET /capabilities through the existing client", async () => {
  const lib = await source("lib/capabilities.ts");
  // Uses the shared, generated contract type for the response — no hand-rolled
  // shape that could drift from the API.
  assert.match(lib, /ApiResponse<"GET \/capabilities">/);
  // Fetched through the existing editorApiRequest client, as a GET.
  assert.match(lib, /import \{ editorApiRequest \} from "@\/lib\/client-api"/);
  assert.match(lib, /editorApiRequest<CapabilitiesResponse>\(/);
  assert.match(lib, /\$\{env\.apiBaseUrl\}\/capabilities/);
  assert.match(lib, /method: "GET"/);
});

test("capabilities client: cached by registry_version and refetched on change", async () => {
  const lib = await source("lib/capabilities.ts");
  // The cache is keyed by the registry version: a changed version replaces the
  // cached catalogue, an unchanged one is reused.
  assert.match(lib, /registryVersion/);
  assert.match(
    lib,
    /cache\.catalog\.registryVersion === catalog\.registryVersion/,
  );
  assert.match(lib, /CAPABILITY_REFRESH_INTERVAL_MS/);
  // The hook periodically re-checks via the editor's blessed polling helper so
  // a registry change is seen without a reload.
  const hook = await source("hooks/useCapabilities.ts");
  assert.match(hook, /startAdaptivePolling/);
  assert.match(hook, /load\(signal, true\)/);
});

test("capabilities client: unreachable API yields a neutral unknown state, never throws", async () => {
  const lib = await source("lib/capabilities.ts");
  // A failed fetch is caught and resolved to the neutral catalogue; the
  // unknown result is not cached so the next attempt retries.
  assert.match(lib, /} catch \{/);
  assert.match(lib, /return cache\?\.catalog \?\? unknownCatalog\(\)/);
  assert.match(lib, /statusUnknown: true/);
  // resolveCapability turns the unknown catalogue into an "unknown" optional
  // state rather than claiming the capability is configured.
  assert.match(lib, /catalog\.statusUnknown \? "unknown" : "unavailable"/);
});

test("capabilities client: the three visible states use the exact plan copy", async () => {
  const lib = await source("lib/capabilities.ts");
  assert.match(lib, new RegExp(UNAVAILABLE_TEXT.replace(/[-—]/g, "\\$&")));
  assert.match(lib, new RegExp(DEGRADED_TEXT));
  // available -> no status text; degraded -> degraded; otherwise unavailable.
  assert.match(lib, /availability === "degraded"/);
  assert.match(
    lib,
    /enabled:\s*availability === "available" \|\| availability === "degraded"/,
  );
});

test("capabilities client type carries no URL, model id or secret", async () => {
  const lib = await source("lib/capabilities.ts");
  // The CapabilityState shape is a strict subset of the public contract.
  const stateType = lib.slice(
    lib.indexOf("export type CapabilityState = {"),
    lib.indexOf("};", lib.indexOf("export type CapabilityState = {")),
  );
  assert.ok(stateType.length > 0, "CapabilityState type should be present");
  for (const forbidden of [
    "url",
    "base_url",
    "baseUrl",
    "model_id",
    "modelId",
    "secret",
    "api_key",
    "apiKey",
    "endpoint",
    "provider",
  ]) {
    assert.ok(
      !new RegExp(`\\b${forbidden}\\b`, "i").test(stateType),
      `CapabilityState must not carry ${forbidden}`,
    );
  }
});

test("capability gate: unavailable control uses aria-disabled and visible text, not colour alone", async () => {
  const gate = await source("components/editor/CapabilityGate.tsx");
  // Status text is rendered as text with an icon — meaning is never carried by
  // colour alone.
  assert.match(gate, /capabilityStatusText\(state\)/);
  assert.match(gate, /role="status"/);
  assert.match(gate, /data-capability-status=\{state\.availability\}/);
  const props = await source("lib/capabilities.ts");
  // The control props expose aria-disabled, never the disabled attribute, so
  // keyboard focus is preserved (the codebase's focus pattern).
  assert.match(props, /"aria-disabled": blocked/);
  assert.match(props, /blocked = !state\.enabled/);
});

test("ScalePanel: generated B-roll gated on video.generation.submit; stems on audio.stem_separation", async () => {
  const panel = await source("components/editor/ScalePanel.tsx");
  assert.match(panel, /CAPABILITY_IDS\.videoGeneration/);
  assert.match(panel, /CAPABILITY_IDS\.stemSeparation/);
  // Both controls carry the capability accessibility attributes and refuse
  // activation when blocked.
  assert.match(
    panel,
    /aria-disabled=\{videoGenerationControl\["aria-disabled"\]\}/,
  );
  assert.match(panel, /if \(videoGenerationControl\.blocked\) return;/);
  assert.match(panel, /aria-disabled=\{stemsControl\["aria-disabled"\]\}/);
  assert.match(panel, /if \(stemsControl\.blocked\) return;/);
  // The status line is rendered beside each control.
  assert.match(
    panel,
    /<CapabilityStatus\s+state=\{videoGenerationCapability\}/,
  );
  assert.match(panel, /<CapabilityStatus state=\{stemsCapability\}/);
});

test("ScalePanel: capability ids reference the Section 2.1 contract ids", async () => {
  const lib = await source("lib/capabilities.ts");
  assert.match(lib, /videoGeneration: "video\.generation\.submit"/);
  assert.match(lib, /stemSeparation: "audio\.stem_separation"/);
});

test("ai-step: visual analysis surfaces object detection, captions and emotion capability state", async () => {
  const step = await source("components/editor/ai-step.tsx");
  assert.match(step, /CAPABILITY_IDS\.objectDetection/);
  assert.match(step, /CAPABILITY_IDS\.captions/);
  assert.match(step, /CAPABILITY_IDS\.emotion/);
  assert.match(step, /<CapabilityStatus\s+state=\{objectDetectionCapability\}/);
  assert.match(step, /<CapabilityStatus\s+state=\{captionsCapability\}/);
  assert.match(step, /<CapabilityStatus\s+state=\{emotionCapability\}/);
});

test("audio tab: the vocal stems section reflects audio.stem_separation state", async () => {
  const tab = await source("components/editor/tabs/audio-tab.tsx");
  assert.match(tab, /CAPABILITY_IDS\.stemSeparation/);
  assert.match(tab, /<CapabilityStatus\s+state=\{stemsCapability\}/);
  // The existing full-mix hint and behaviour are preserved (plan Section 3).
  assert.match(tab, /Separate vocals and music in Intelligence/);
});

test("look tab: subject segmentation is gated on matte.segmentation", async () => {
  const tab = await source("components/editor/tabs/look-tab.tsx");
  assert.match(tab, /CAPABILITY_IDS\.matte/);
  assert.match(tab, /aria-disabled=\{matteControl\["aria-disabled"\]\}/);
  assert.match(tab, /if \(matteControl\.blocked\) return;/);
  assert.match(tab, /<CapabilityStatus\s+state=\{matteCapability\}/);
});

test("useCapabilities hook: reusable catalog + resolver surface for the model-picker task", async () => {
  const hook = await source("hooks/useCapabilities.ts");
  // The hook returns the whole catalogue and a resolver — exactly what a model
  // picker (Section 2.11) reads to show per-capability status.
  assert.match(hook, /catalog: CapabilityCatalog/);
  assert.match(hook, /resolve: \(id: string\) => CapabilityState/);
  assert.match(hook, /statusUnknown: boolean/);
  assert.match(hook, /refresh: \(\) => Promise<void>/);
  // It never throws: resolve always returns a renderable state.
  assert.match(hook, /resolveCapability\(catalog, id\)/);
});
