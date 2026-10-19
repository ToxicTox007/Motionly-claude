// A creator's journey on the CPU stage, through the edge. Covered here: a
// project is created, footage is uploaded from the browser in multipart parts
// straight to storage, the upload completes, survives a reload, and the
// Temporal-driven first draft starts processing.
//
// Not covered yet: preprocessing through to the editor, render, playback and
// download. The editor step is an expected failure below (Task 15); render,
// playback and download are added with Tasks 15-16.
import { createSourceVideo } from "./support/media.mjs";
import { expect, signIn, test } from "./support/fixtures.mjs";

let media;

test.beforeAll(() => {
  media = createSourceVideo();
});

test.afterAll(() => media?.remove());

test("a creator uploads footage from the browser and the first draft starts processing", async ({
  page,
  user,
  gates,
}) => {
  const uploadCalls = [];
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (/\/api\/projects\/[^/]+\/uploads\//.test(path)) {
      uploadCalls.push(
        `${response.status()} ${response.request().method()} ${path.replace(/[0-9a-f-]{36}/g, ":id")}`,
      );
    }
  });

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Browser creator journey");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(
    page.getByRole("heading", { name: "Upload your footage" }),
  ).toBeVisible();

  await page
    .getByLabel("Choose a video file to upload")
    .setInputFiles(media.path);
  await page.getByRole("button", { name: /^Upload$/ }).click();

  // The browser initialises a multipart session, sends the part to storage
  // through the edge, and completes it; no legacy single-shot completion.
  await expect
    .poll(() => uploadCalls, { timeout: 30_000 })
    .toEqual(
      expect.arrayContaining([
        "201 POST /api/projects/:id/uploads/multipart/init",
        "200 POST /api/projects/:id/uploads/multipart/:id/parts/1/complete",
        "200 POST /api/projects/:id/uploads/multipart/:id/complete",
      ]),
    );
  expect(uploadCalls.filter((call) => /^[45]/.test(call))).toEqual([]);

  // The upload survives a reload: the page reads its state from the server
  // and shows the processing stages instead of the upload form.
  await page.reload();
  await expect(
    page.getByText("Processing stages", { exact: false }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Upload your footage" }),
  ).toHaveCount(0);
  await gates.checkAccessibility(page);
});

// Known defect, fixed in Task 15: on the CPU stage the preprocessing worker
// refuses the internal signed link to the uploaded media
// (worker_media_signing_invalid), and the retried step can never be enqueued
// (WORKFLOW_STEP_TRANSPORT_CONFLICT), so the first draft fails before the
// editor. This test is expected to fail until then; when it starts passing,
// Playwright fails the run so the marker is removed.
test("the uploaded footage reaches the editor", async ({ page, user }) => {
  test.fail(true, "Task 15: CPU-stage preprocessing fails before the editor");
  test.setTimeout(240_000);

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Browser journey to the editor");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await page
    .getByLabel("Choose a video file to upload")
    .setInputFiles(media.path);
  await page.getByRole("button", { name: /^Upload$/ }).click();

  await expect(async () => {
    await page.reload();
    await expect(page.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 200_000, intervals: [10_000] });
});
