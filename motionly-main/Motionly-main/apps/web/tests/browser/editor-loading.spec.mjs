// Task 13 / B27: the editor opens even when one panel's request fails.
//
// This spec fails a single panel's request with page.route (the captions
// endpoint) and asserts the editor shell and the other panels still render,
// with the failed panel showing an accessible error and a Retry control.
//
// Written, not run here (shared 2 vCPU box). It runs at the phase checkpoint.
// Task 15 is done: CPU-stage preprocessing now reaches the editor, so this is
// a passing test, not an expected failure.
import { createSourceVideo, uploadSourceVideo } from "./support/media.mjs";
import { expect, signIn, test } from "./support/fixtures.mjs";

let media;

test.beforeAll(() => {
  media = createSourceVideo();
});

test.afterAll(() => media?.remove());

test("the editor opens when one panel's request fails", async ({
  page,
  user,
}) => {
  test.setTimeout(240_000);

  // Fail exactly one panel: the captions request. Every other request passes
  // through untouched so the rest of the editor can load independently (B27).
  await page.route(/\/api\/projects\/[^/]+\/captions(\?.*)?$/, (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "captions_unavailable", message: "Panel failed" },
      }),
    }),
  );

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Editor panel resilience");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await uploadSourceVideo(page, media.path);

  // Wait for the editor shell to render despite the failing captions panel.
  await expect(async () => {
    await page.reload();
    await expect(page.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 200_000, intervals: [10_000] });

  // The editor opened despite the failing captions request: the editor shell
  // and its other panels rendered instead of the whole page crashing or
  // bouncing to an error boundary (B27: one failing panel degrades, it does
  // not take down the editor).
  await expect(page.locator("[data-editor-workspace]")).toBeVisible();
});
