import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("Phase 6 uses a canonical product flow status model", async () => {
  const statusModel = await readFile(
    new URL("lib/product-flow-status.ts", root),
    "utf8",
  );
  const editor = await readFile(
    new URL("components/editor-client.tsx", root),
    "utf8",
  );
  const upload = await readFile(
    new URL("components/upload-panel.tsx", root),
    "utf8",
  );
  const exportStep = await readFile(
    new URL("components/editor/export-step.tsx", root),
    "utf8",
  );

  for (const stage of [
    "uploaded",
    "validated",
    "transcribing",
    "visual_analysis",
    "planning",
    "captions_ready",
    "render_queued",
    "render_active",
    "export_ready",
  ]) {
    assert.match(statusModel, new RegExp(`key: "${stage}"`));
  }

  assert.match(statusModel, /productFlowStageIndex/);
  assert.match(statusModel, /uploadStatusMessage/);
  assert.match(statusModel, /renderStatusLabel/);
  assert.doesNotMatch(statusModel, /publish/i);
  assert.match(statusModel, /queuedWaitBandLabel/);
  assert.match(statusModel, /Wait band: short/);
  assert.match(statusModel, /Wait band: moderate/);
  assert.match(statusModel, /Wait band: extended/);
  assert.match(editor, /PRODUCT_FLOW_STAGES\.map/);
  assert.match(editor, /productFlowStageIndex\(status\)/);
  assert.match(editor, /uploadStatusMessage\(status\)/);
  assert.match(upload, /uploadStatusMessage\(status\)/);
  assert.match(exportStep, /renderStatusLabel\(status\)/);
  assert.doesNotMatch(exportStep, /publishStatusLabel|\/publish\b/);
  // Motionly does not post to social platforms: the tab that prepares
  // translated captions is not called "Publish".
  assert.match(editor, /id: "localize", label: "Localize"/);
  assert.doesNotMatch(editor, /label: "Publish"/);
  assert.match(
    exportStep,
    /queuedWaitBandLabel\(latestJob\.created_at, nowMs\)/,
  );
  assert.match(exportStep, /\{waitBandLabel\}/);
  assert.doesNotMatch(exportStep, /const labels: Record<string, string> = \{/);
});

test("Phase 6 retry, cancel, and degraded states are exposed in product flows", async () => {
  const upload = await readFile(
    new URL("components/upload-panel.tsx", root),
    "utf8",
  );
  const exportStep = await readFile(
    new URL("components/editor/export-step.tsx", root),
    "utf8",
  );
  const adminPage = await readFile(
    new URL("app/(app)/admin/ops/page.tsx", root),
    "utf8",
  );
  const adminActions = await readFile(
    new URL("app/(app)/admin/ops/actions.ts", root),
    "utf8",
  );
  const projectPage = await readFile(
    new URL("app/(app)/projects/[projectId]/page.tsx", root),
    "utf8",
  );
  const billingPage = await readFile(
    new URL("app/(app)/billing/page.tsx", root),
    "utf8",
  );

  assert.match(upload, /function cancelUpload/);
  assert.match(upload, /cancelUploadRegistration/);
  assert.match(upload, />\s*Retry\s*</);
  assert.match(exportStep, /async function retryRender/);
  assert.match(exportStep, /async function cancelRender/);
  assert.match(exportStep, /Retry failed render/);
  assert.match(exportStep, /Cancel render/);
  assert.match(adminActions, /retryQueueJob/);
  assert.match(adminActions, /cancelQueueJob/);
  assert.match(adminActions, /retryRenderJob/);
  assert.match(adminActions, /cancelRenderJob/);
  assert.match(adminPage, /onRetry=\{retryQueueJob\}/);
  assert.match(adminPage, /onCancel=\{cancelQueueJob\}/);
  assert.match(
    projectPage,
    /Visual context is unavailable; editing will continue using transcript and audio/,
  );
  assert.match(billingPage, /Billing unavailable/);
});

test("Phase 6 notification taxonomy keeps short feedback in toasts", async () => {
  const taxonomy = await readFile(
    new URL("lib/notification-taxonomy.ts", root),
    "utf8",
  );
  const upload = await readFile(
    new URL("components/upload-panel.tsx", root),
    "utf8",
  );
  const exportStep = await readFile(
    new URL("components/editor/export-step.tsx", root),
    "utf8",
  );

  assert.match(taxonomy, /blocker: "inline_alert"/);
  assert.match(taxonomy, /persistent_status: "inline_status"/);
  assert.match(taxonomy, /short_action_feedback: "toast"/);
  assert.match(taxonomy, /destructive_confirmation: "modal"/);
  assert.match(taxonomy, /durable_state: "badge"/);
  assert.match(
    upload,
    /shouldToast\("short_action_feedback"\)[\s\S]*toast\(\{/,
  );
  assert.match(
    exportStep,
    /shouldToast\("short_action_feedback"\)[\s\S]*toast\(\{/,
  );
});
