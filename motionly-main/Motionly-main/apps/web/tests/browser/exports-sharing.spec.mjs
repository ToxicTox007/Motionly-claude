// Finished videos: play, download, share, watch signed-out, download
// signed-out, revoke, and confirm the link stops working (Task 16).
//
// The whole journey depends on a real finished export from the CPU-stage render
// pipeline. Task 15 is done: the preprocessing worker now accepts the internal
// signed link, a failed step can be re-enqueued, and renders complete end to
// end (see creator-journey.spec.mjs "the uploaded footage reaches the editor"),
// so this spec produces a real export to share and is a passing test.
import { createSourceVideo, uploadSourceVideo } from "./support/media.mjs";
import { expect, signIn, test } from "./support/fixtures.mjs";
import { stackCredentials } from "./support/stack.mjs";

let media;

test.beforeAll(() => {
  media = createSourceVideo();
});

test.afterAll(() => media?.remove());

test("a finished export is played, downloaded, shared, watched, and revoked", async ({
  page,
  context,
  playwright,
  user,
}) => {
  test.setTimeout(300_000);

  // 1. Create a project and upload footage.
  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Exports sharing journey");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await uploadSourceVideo(page, media.path);
  const projectId = new URL(page.url()).pathname.split("/").pop();

  // 2. Reach the editor and render an export (Task 15's real render pipeline).
  await expect(async () => {
    await page.reload();
    await expect(page.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 240_000, intervals: [10_000] });
  await page.getByRole("button", { name: /^Start Render$/ }).click();
  await expect(page.getByText(/Export is ready to download/i)).toBeVisible({
    timeout: 240_000,
  });

  // 3. Open the Exports panel and play the newest version in the in-app player.
  await page.getByRole("button", { name: /Load player for v1/i }).click();
  const player = page.getByLabel(/Export version 1 player/i);
  await expect(player).toBeVisible();
  // The player can seek: set a non-zero playback position.
  await player.evaluate((element) => {
    element.currentTime = 1;
  });
  expect(
    await player.evaluate((element) => element.currentTime),
  ).toBeGreaterThan(0);

  // 4. Download the version; a signed link with a filename is produced.
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /Download export version 1/i }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/-v1\.mp4$/);

  // 5. Create a share link that allows downloads and copy it (desktop path).
  await page.getByRole("button", { name: /Share v1/i }).click();
  await page.getByLabel("Allow viewers to download").check();
  let copiedUrl = "";
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: /Create & share link/i }).click();
  await expect
    .poll(async () => {
      copiedUrl = await page.evaluate(() => navigator.clipboard.readText());
      return copiedUrl;
    })
    .toMatch(/\/watch#t=/);

  // 6. A signed-out viewer opens the watch page and watches the video.
  const viewer = await playwright.request.newContext();
  const guestContext = await page.context().browser().newContext();
  const guest = await guestContext.newPage();
  await guest.goto(copiedUrl);
  const guestPlayer = guest.getByLabel(/video player/i);
  await expect(guestPlayer).toBeVisible();

  // 7. The signed-out viewer downloads the video.
  const [guestDownload] = await Promise.all([
    guest.waitForEvent("download"),
    guest.getByRole("link", { name: /^Download$/ }).click(),
  ]);
  expect(guestDownload.suggestedFilename()).toMatch(/\.mp4$/);
  await viewer.dispose();

  // 8. Revoke the link from the owner's Exports panel.
  await page
    .getByRole("button", { name: /^Revoke$/ })
    .first()
    .click();
  await expect(page.getByText(/no longer works/i)).toBeVisible();

  // 9. The revoked link stops working: a fresh viewer gets the neutral error.
  const revokedContext = await page.context().browser().newContext();
  const revoked = await revokedContext.newPage();
  await revoked.goto(copiedUrl);
  await expect(
    revoked.getByText(/invalid, expired, or has been revoked/i),
  ).toBeVisible();
  await revokedContext.close();
  await guestContext.close();
});

// Enumeration safety at the edge: an unknown token resolves to the same neutral
// error a revoked or expired token does. This does not need a render, so it is
// not gated on Task 15 — it exercises the public resolve endpoint directly.
test("an unknown share token shows the same neutral error", async ({
  page,
}) => {
  // Confirm the stack credentials are available so the suite fails loudly if
  // the public endpoint is unreachable rather than silently passing.
  stackCredentials();
  await page.goto("/watch#t=this-token-never-existed");
  await expect(
    page.getByText(/invalid, expired, or has been revoked/i),
  ).toBeVisible();
});
