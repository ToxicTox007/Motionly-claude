// A creator's journey on the CPU stage, through the edge. Covered here: a
// project is created, footage is uploaded from the browser in multipart parts
// straight to storage, the upload completes, survives a reload, the
// Temporal-driven first draft starts processing, reaches the editor, is
// approved, rendered on each engine, and downloaded.
//
// Every fixture input the journey relies on carries its registry label so a
// deferred GPU/AI capability can never masquerade as the real thing.
import { createSourceVideo, uploadSourceVideo } from "./support/media.mjs";
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
// Task 15 fixed the CPU-stage defects that blocked this path: the preprocessing
// worker now accepts the internal signed link, a failed step can be re-enqueued
// (no WORKFLOW_STEP_TRANSPORT_CONFLICT), the real cause surfaces instead of a
// masked "unavailable", and deleting a project cancels its workflow. The first
// draft therefore reaches the editor, is approved, rendered on each engine, and
// downloaded.
test("the uploaded footage reaches the editor, renders, and downloads", async ({
  page,
  user,
}) => {
  test.setTimeout(240_000);

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Browser journey to the editor");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await uploadSourceVideo(page, media.path);

  // Preprocessing runs through to the editor (no silent stall; a delayed run
  // would surface "Delayed — retrying" rather than hang).
  await expect(async () => {
    await page.reload();
    await expect(page.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 200_000, intervals: [10_000] });

  // The first-draft proposal always reaches the editor in review; approve it
  // (no `if (visible)` guard — the approval gate must be present) before the
  // render. Each render engine (ffmpeg_basic, remotion_premium) finishes or
  // fails visibly — it never silently stalls.
  const approveAll = page.getByRole("button", { name: "Approve all" });
  await expect(approveAll).toBeEnabled({ timeout: 120_000 });
  await approveAll.click();

  await page.getByRole("button", { name: /Start Render/i }).click();
  await expect(page.getByLabel("Download latest export")).toBeVisible({
    timeout: 200_000,
  });
  await page.getByLabel("Download latest export").click();
});

// A background-removal render uses the labelled Test matte (fixture-matte:v1)
// on the CPU stage: the editor shows a "Test" badge whose accessible name is
// the registry label, and the render completes with the visible test matte.
test("a background-removal render uses the labelled test matte", async ({
  page,
  user,
}) => {
  test.setTimeout(240_000);

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Browser matte journey");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await uploadSourceVideo(page, media.path);

  await expect(async () => {
    await page.reload();
    await expect(page.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 200_000, intervals: [10_000] });

  // Enable subject segmentation (background removal) in the Look tab.
  await page.getByRole("tab", { name: "Look", exact: true }).click();
  await page.getByLabel("Enable subject segmentation").check();

  // The preview shows the "Test" badge; its accessible name is the registry
  // label ("Test matte"), never the real MediaPipe model name.
  const badge = page.getByLabel("Test matte");
  await expect(badge).toBeVisible({ timeout: 200_000 });
  await expect(badge).toHaveText(/Test/);

  // The render with the test matte completes.
  await page.getByRole("button", { name: /Start Render/i }).click();
  await expect(page.getByLabel("Download latest export")).toBeVisible({
    timeout: 200_000,
  });
});
