// Task 30: applying a creative variant changes the edit.
//
// Reaches the editor from a real CPU-stage upload, approves the first-draft
// proposal so there is an active plan, generates a small creative-variant
// review set, and then APPLIES one direction. Applying is not a review-only
// bookmark: it creates a new plan version from the chosen variant, so the panel
// confirms the edit changed. A second pass applies-and-renders the variant.
//
// Written, not run here (shared 2 vCPU box; --list only). It runs at the phase
// checkpoint. Task 15 is done, so preprocessing reaches the editor and renders
// complete end to end, making this a passing test rather than an expected
// failure.
import { createSourceVideo, uploadSourceVideo } from "./support/media.mjs";
import { expect, signIn, test } from "./support/fixtures.mjs";

let media;

test.beforeAll(() => {
  media = createSourceVideo();
});

test.afterAll(() => media?.remove());

test("applying a creative variant creates a new plan version from that variant", async ({
  page,
  user,
}) => {
  test.setTimeout(240_000);

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Creative variant apply journey");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await uploadSourceVideo(page, media.path);

  await expect(async () => {
    await page.reload();
    await expect(page.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 200_000, intervals: [10_000] });

  // A first-draft proposal always reaches the editor in review; approving it
  // is what creates the active plan the creative-variants panel operates on.
  // No `if (visible)` guard: the approval gate must be present.
  const approveAll = page.getByRole("button", { name: "Approve all" });
  await expect(approveAll).toBeEnabled({ timeout: 120_000 });
  await approveAll.click();

  // The creative-variants panel is part of the Scale panel in the editor.
  const panel = page.getByTestId("creative-variants");
  await expect(panel).toBeVisible({ timeout: 60_000 });

  // Generate a small review set.
  await panel.getByRole("button", { name: /Generate review set/i }).click();

  // Apply the first variant's direction. The accessible label names the
  // variant it applies, and the panel confirms a new plan version was created.
  const applyButton = panel
    .getByRole("button", { name: /^Apply .* to the edit$/ })
    .first();
  await expect(applyButton).toBeEnabled({ timeout: 60_000 });
  await applyButton.click();

  await expect(
    panel.getByText(/A new plan version is ready to preview/i),
  ).toBeVisible({ timeout: 60_000 });
  await expect(panel.getByText(/direction applied to the edit/i)).toBeVisible();
});

test("apply and render queues a render of the new variant version", async ({
  page,
  user,
}) => {
  test.setTimeout(240_000);

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Creative variant render journey");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await uploadSourceVideo(page, media.path);

  await expect(async () => {
    await page.reload();
    await expect(page.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 200_000, intervals: [10_000] });

  // Approve the first-draft proposal to create the active plan (required; no
  // `if (visible)` guard).
  const approveAll = page.getByRole("button", { name: "Approve all" });
  await expect(approveAll).toBeEnabled({ timeout: 120_000 });
  await approveAll.click();

  const panel = page.getByTestId("creative-variants");
  await expect(panel).toBeVisible({ timeout: 60_000 });
  await panel.getByRole("button", { name: /Generate review set/i }).click();

  const applyAndRender = panel
    .getByRole("button", { name: /^Apply .* and render the new version$/ })
    .first();
  await expect(applyAndRender).toBeEnabled({ timeout: 60_000 });
  await applyAndRender.click();

  await expect(
    panel.getByText(/a render was queued for the new version/i),
  ).toBeVisible({ timeout: 120_000 });
});
