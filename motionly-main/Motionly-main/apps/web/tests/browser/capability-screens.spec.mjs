// Task 31: every capability the code supports can be reached from the app.
// One test per control. Each test drives the real UI control and asserts the
// real API route it is wired to is called (or, for the admin control, that its
// access guard behaves), with no hollow `if (visible)` guards.
//
// Written and `--list`-checked here; the suite runs at the phase checkpoint on
// the CPU stage. Editor controls are reached by opening a project; the project
// data the specific control needs is served deterministically with page.route
// so the control renders without depending on a full render pipeline, exactly
// as the gates require (state is created, never guarded around).
//
// Admin configuration note (same constraint as password-reset.spec.mjs): the
// CPU stage cannot promote a per-test user to platform admin (ADMIN_USER_IDS
// is set at container start, before this test's users exist). So the
// dead-letter-stuck control's render-and-click path is covered by
// apps/web/tests/task31-capability-screens.test.mjs and apps/api/tests
// test_admin.py; the browser spec asserts its access guard — a non-admin is
// redirected away from /admin/ops rather than seeing the control.
import { expect, signIn, test } from "./support/fixtures.mjs";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const EDITOR_PATH = `/projects/${PROJECT_ID}`;

/**
 * Open the editor for a fixed project id with the per-panel data the control
 * under test needs stubbed, so exactly one control is exercised per test.
 */
async function openEditorWithStubs(page, user, stubs) {
  for (const [pattern, handler] of stubs) {
    await page.route(pattern, handler);
  }
  await signIn(page, user, EDITOR_PATH);
  await expect(page.locator("[data-editor-workspace]")).toBeVisible({
    timeout: 60_000,
  });
}

function json(body, status = 200) {
  return (route) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
}

test("Audio tab: choosing the instrumental stem persists it via the select route", async ({
  page,
  user,
}) => {
  test.setTimeout(120_000);
  const stemId = "22222222-2222-4222-8222-222222222222";
  const stemResource = {
    id: stemId,
    kind: "stem_separation",
    name: "Source audio stems",
    status: "ready",
    revision: 1,
    payload: {},
    result: {
      active: "vocals",
      stem_provider: "fixture",
      vocals: { bucket: "raw-uploads", path: "stems/vocals.wav" },
      no_vocals: { bucket: "raw-uploads", path: "stems/no_vocals.wav" },
    },
  };
  let patched = null;
  await openEditorWithStubs(page, user, [
    [
      /\/api\/projects\/[^/]+\/phase5\/resources\?kind=stem_separation$/,
      json({ resources: [stemResource] }),
    ],
    [
      new RegExp(`/api/projects/[^/]+/phase5/stems/${stemId}$`),
      async (route) => {
        patched = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ...stemResource,
            revision: 2,
            result: { ...stemResource.result, active: "no_vocals" },
          }),
        });
      },
    ],
  ]);

  await page.getByRole("tab", { name: /Audio/i }).click();
  // The fixture separator's badge is shown next to the stem choice.
  await expect(page.getByText("Test stems", { exact: true })).toBeVisible();
  await page
    .getByRole("radio", { name: /Instrumental only \(no vocals\)/i })
    .click();

  await expect.poll(() => patched?.active).toBe("no_vocals");
  expect(patched.expected_revision).toBe(1);
});

test("Editing: the regenerate control rebuilds the chosen layers", async ({
  page,
  user,
}) => {
  test.setTimeout(120_000);
  let regenBody = null;
  await openEditorWithStubs(page, user, [
    [
      /\/api\/projects\/[^/]+\/regenerate$/,
      async (route) => {
        regenBody = route.request().postDataJSON();
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            status: "queued",
            job_id: "job-1",
            affected_layers: ["captions"],
            preserved_layers: [],
            controls: {},
            artifact_versions: [],
          }),
        });
      },
    ],
  ]);

  await page.getByRole("tab", { name: /Scale|Intelligence/i }).click();
  await page.getByRole("checkbox", { name: /Regenerate Captions/i }).check();
  await page
    .getByRole("button", { name: /Regenerate selected layers/i })
    .click();

  await expect.poll(() => regenBody?.layers).toContain("captions");
});

test("Editing: a saved template can be renamed", async ({ page, user }) => {
  test.setTimeout(120_000);
  const templateId = "33333333-3333-4333-8333-333333333333";
  const template = {
    id: templateId,
    kind: "saved_template",
    name: "Launch cut",
    status: "ready",
    revision: 0,
    payload: { settings: {} },
    result: {},
  };
  let renamed = null;
  await openEditorWithStubs(page, user, [
    [/\/api\/phase5\/templates$/, json({ resources: [template] })],
    [
      new RegExp(`/api/phase5/templates/${templateId}$`),
      async (route) => {
        if (route.request().method() === "PATCH") {
          renamed = route.request().postDataJSON();
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ ...template, name: renamed.name }),
          });
        }
        return route.fallback();
      },
    ],
  ]);

  await page.getByRole("tab", { name: /Scale|Intelligence/i }).click();
  await page.getByRole("button", { name: /Rename Launch cut/i }).click();
  await page
    .getByRole("textbox", { name: /New name for Launch cut/i })
    .fill("Launch cut v2");
  await page.getByRole("button", { name: /Save name/i }).click();

  await expect.poll(() => renamed?.name).toBe("Launch cut v2");
});

test("Editing: deleting a saved template asks for confirmation first", async ({
  page,
  user,
}) => {
  test.setTimeout(120_000);
  const templateId = "44444444-4444-4444-8444-444444444444";
  const template = {
    id: templateId,
    kind: "saved_template",
    name: "Old cut",
    status: "ready",
    revision: 0,
    payload: { settings: {} },
    result: {},
  };
  let deleteCalled = false;
  await openEditorWithStubs(page, user, [
    [/\/api\/phase5\/templates$/, json({ resources: [template] })],
    [
      new RegExp(`/api/phase5/templates/${templateId}$`),
      async (route) => {
        if (route.request().method() === "DELETE") {
          deleteCalled = true;
          return route.fulfill({ status: 204, body: "" });
        }
        return route.fallback();
      },
    ],
  ]);

  await page.getByRole("tab", { name: /Scale|Intelligence/i }).click();
  await page.getByRole("button", { name: /Delete Old cut/i }).click();
  // The destructive action is confirmed, not performed immediately.
  const dialog = page.getByRole("alertdialog", {
    name: /Confirm deleting template Old cut/i,
  });
  await expect(dialog).toBeVisible();
  expect(deleteCalled).toBe(false);
  await dialog.getByRole("button", { name: /Confirm delete/i }).click();

  await expect.poll(() => deleteCalled).toBe(true);
});

test("Other: a running batch can be canceled", async ({ page, user }) => {
  test.setTimeout(120_000);
  const batchId = "55555555-5555-4555-8555-555555555555";
  const batch = {
    id: batchId,
    kind: "batch",
    name: "Same-style batch",
    status: "processing",
    revision: 0,
    payload: {},
    result: {},
  };
  let cancelCalled = false;
  await openEditorWithStubs(page, user, [
    [
      /\/api\/projects\/[^/]+\/phase5\/resources(\?.*)?$/,
      json({ resources: [batch] }),
    ],
    [
      new RegExp(`/api/phase5/batches/${batchId}/cancel$`),
      async (route) => {
        cancelCalled = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ...batch, status: "canceled" }),
        });
      },
    ],
  ]);

  await page.getByRole("tab", { name: /Scale|Intelligence/i }).click();
  await page
    .getByRole("button", { name: /Cancel batch Same-style batch/i })
    .click();

  await expect.poll(() => cancelCalled).toBe(true);
});

test("Other: an asset-library item can be previewed", async ({
  page,
  user,
}) => {
  test.setTimeout(120_000);
  const assetId = "66666666-6666-4666-8666-666666666666";
  const asset = {
    id: assetId,
    kind: "broll",
    name: "City skyline",
    description: "Generated B-roll",
    is_favorite: false,
    source_project_id: PROJECT_ID,
  };
  await openEditorWithStubs(page, user, [
    [/\/api\/asset-library\/items(\?.*)?$/, json({ items: [asset] })],
    [/\/api\/asset-library\/collections$/, json({ collections: [] })],
    [
      /\/api\/projects\/[^/]+\/localized-publish-packages$/,
      json({ packages: [] }),
    ],
    [
      new RegExp(`/api/asset-library/items/${assetId}/media-url$`),
      json({
        object_reference_id: assetId,
        url: "https://cdn.example.test/skyline.mp4",
        expires_at: "2099-01-01T00:00:00Z",
        ttl_seconds: 600,
      }),
    ],
  ]);

  await page.getByRole("tab", { name: /Publish|Library/i }).click();
  await page.getByRole("button", { name: /Preview City skyline/i }).click();

  await expect(
    page.getByRole("dialog", { name: /Preview of City skyline/i }),
  ).toBeVisible();
});

test("Other: an asset-library item can be downloaded", async ({
  page,
  user,
}) => {
  test.setTimeout(120_000);
  const assetId = "77777777-7777-4777-8777-777777777777";
  const asset = {
    id: assetId,
    kind: "broll",
    name: "skyline.mp4",
    description: "Generated B-roll",
    is_favorite: false,
    source_project_id: PROJECT_ID,
  };
  await openEditorWithStubs(page, user, [
    [/\/api\/asset-library\/items(\?.*)?$/, json({ items: [asset] })],
    [/\/api\/asset-library\/collections$/, json({ collections: [] })],
    [
      /\/api\/projects\/[^/]+\/localized-publish-packages$/,
      json({ packages: [] }),
    ],
    [
      new RegExp(`/api/asset-library/items/${assetId}/media-url$`),
      json({
        object_reference_id: assetId,
        url: "https://cdn.example.test/skyline.mp4",
        expires_at: "2099-01-01T00:00:00Z",
        ttl_seconds: 600,
      }),
    ],
    [
      "https://cdn.example.test/skyline.mp4",
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "video/mp4",
          body: Buffer.from("fake-mp4"),
        }),
    ],
  ]);

  await page.getByRole("tab", { name: /Publish|Library/i }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /Download skyline\.mp4/i }).click();
  const saved = await download;
  expect(saved.suggestedFilename()).toBe("skyline.mp4");
});

test("Admin: a non-admin cannot reach the dead-letter-stuck control", async ({
  page,
  user,
}) => {
  // The dead-letter-stuck button lives on /admin/ops behind getAdminApiContext.
  // A normal owner is redirected to the dashboard with an access-denied error,
  // so the control is unreachable to non-admins. The render-and-click path is
  // covered by the unit (task31-capability-screens.test.mjs) and API
  // (test_admin.py) tests per the admin configuration note above.
  await signIn(page, user, "/dashboard");
  await page.goto("/admin/ops");
  await expect(page).toHaveURL(/\/dashboard\?error=admin_access_denied/);
  await expect(
    page.getByRole("button", { name: /Dead-letter stuck jobs/i }),
  ).toHaveCount(0);
});

// Task 9B: optional-capability controls are gated on the public /capabilities
// contract. When a capability is not configured, its control shows the neutral
// unavailable state (visible text + aria-disabled), while unrelated editing on
// the same screen keeps working.
test("Gating: an unavailable capability disables its control with a visible reason", async ({
  page,
  user,
}) => {
  test.setTimeout(120_000);
  await openEditorWithStubs(page, user, [
    [
      /\/api\/capabilities$/,
      json({
        registry_version: "test-v1",
        capabilities: [
          {
            id: "video.generation.submit",
            status: "unavailable",
            reason: "not_configured",
            is_async: true,
            compute_requirement: "remote",
            route_count: 0,
            required: false,
          },
          {
            id: "audio.stem_separation",
            status: "available",
            reason: null,
            is_async: false,
            compute_requirement: "cpu",
            route_count: 1,
            required: false,
          },
        ],
      }),
    ],
  ]);

  await page.getByRole("tab", { name: /Scale|Intelligence/i }).click();

  // The Generated B-roll control is marked unavailable with the exact copy and
  // aria-disabled, and never the disabled attribute (focus is preserved).
  const brollButton = page.getByRole("button", {
    name: /Generate premium B-roll/i,
  });
  await expect(brollButton).toHaveAttribute("aria-disabled", "true");
  await expect(
    page
      .locator('[data-capability="video.generation.submit"]')
      .getByText(/Unavailable — not configured by your operator/),
  ).toBeVisible();

  // A sibling available capability's control is not marked unavailable.
  const stemsButton = page.getByRole("button", {
    name: /Separate vocals and music/i,
  });
  await expect(stemsButton).toHaveAttribute("aria-disabled", "false");
});
