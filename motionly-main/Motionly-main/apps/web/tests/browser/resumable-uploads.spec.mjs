// Task 17: one resumable, server-verified upload path for every media type.
// Each media type (source video, B-roll library clip, style reference) drives
// the shared lib/resumable-upload.ts helper against its resumable multipart
// endpoints. Here we take the browser offline mid-upload (context.setOffline)
// and prove the upload resumes from server-confirmed progress once the network
// returns — never restarting from zero and never losing confirmed parts.
//
// Written for the phase checkpoint; not run here (CONTEXT: browser specs run
// later under the heavy lock). Uses support/media.mjs for the test media.
import { createSourceVideo } from "./support/media.mjs";
import { expect, signIn, test } from "./support/fixtures.mjs";

let media;

test.beforeAll(() => {
  media = createSourceVideo();
});

test.afterAll(() => media?.remove());

// Collect the upload API call ledger so each assertion can prove the resumable
// endpoints were used and that completion happened exactly once.
function trackUploadCalls(page, pattern) {
  const calls = [];
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (pattern.test(path)) {
      calls.push(
        `${response.status()} ${response.request().method()} ${path.replace(
          /[0-9a-f-]{36}/g,
          ":id",
        )}`,
      );
    }
  });
  return calls;
}

test("a source-video upload interrupted by going offline resumes to completion", async ({
  page,
  context,
  user,
  gates,
}) => {
  test.setTimeout(240_000);
  const uploadCalls = trackUploadCalls(
    page,
    /\/api\/projects\/[^/]+\/uploads\/multipart\//,
  );

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Offline resume source video");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(
    page.getByRole("heading", { name: "Upload your footage" }),
  ).toBeVisible();

  await page
    .getByLabel("Choose a video file to upload")
    .setInputFiles(media.path);
  await page.getByRole("button", { name: /^Upload$/ }).click();

  // The resumable session is initialised before any signed URL is used.
  await expect
    .poll(() => uploadCalls, { timeout: 30_000 })
    .toEqual(
      expect.arrayContaining([
        "201 POST /api/projects/:id/uploads/multipart/init",
      ]),
    );

  // Take the browser offline mid-flight. The in-flight part fails, but the
  // durable server session and the confirmed byte count survive.
  await context.setOffline(true);
  await expect(
    page.getByText(/connection|offline|interrupted|failed|retry/i).first(),
  ).toBeVisible({ timeout: 30_000 });

  // Restore the network and resume. Because progress is read from the server,
  // the upload continues from confirmed parts and completes exactly once.
  await context.setOffline(false);
  await page.getByRole("button", { name: /^(Resume upload|Retry)$/ }).click();

  await expect
    .poll(() => uploadCalls, { timeout: 120_000 })
    .toEqual(
      expect.arrayContaining([
        expect.stringMatching(
          /^200 POST \/api\/projects\/:id\/uploads\/multipart\/:id\/complete$/,
        ),
      ]),
    );
  // The session is resumed, never re-initialised from scratch.
  expect(
    uploadCalls.filter((call) => /uploads\/multipart\/init$/.test(call)).length,
    "the interrupted upload resumed its existing session",
  ).toBe(1);
  expect(uploadCalls.filter((call) => /^[45]\d\d POST/.test(call))).not.toEqual(
    expect.arrayContaining([
      expect.stringMatching(/multipart\/:id\/complete$/),
    ]),
  );
  await gates.checkAccessibility(page);
});

// B-roll library clips upload through the same shared helper and the B-roll
// resumable multipart endpoints. Reaching the editor (where the B-roll upload
// control lives) is reachable now that Task 15 landed (CPU-stage preprocessing
// reaches the editor), so this B-roll resume journey is a passing test.
test("a B-roll library upload interrupted by going offline resumes to completion", async ({
  page,
  context,
  user,
}) => {
  test.setTimeout(300_000);
  const uploadCalls = trackUploadCalls(
    page,
    /\/api\/projects\/[^/]+\/broll\/library\/multipart\//,
  );

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Offline resume B-roll");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await page
    .getByLabel("Choose a video file to upload")
    .setInputFiles(media.path);
  await page.getByRole("button", { name: /^Upload$/ }).click();
  await page.reload();

  // Open the editor and the B-roll ("My B-roll") upload control.
  await page
    .getByRole("link", { name: /editor|edit/i })
    .first()
    .click();
  const brollUpload = page
    .getByRole("button", { name: /^Upload$/ })
    .filter({ hasText: /Upload/ })
    .first();
  await brollUpload.scrollIntoViewIfNeeded();
  await page.getByText("My B-roll").scrollIntoViewIfNeeded();
  await page.setInputFiles('input[type="file"][accept*="video"]', media.path);

  await expect
    .poll(() => uploadCalls, { timeout: 30_000 })
    .toEqual(
      expect.arrayContaining([
        "201 POST /api/projects/:id/broll/library/multipart/init",
      ]),
    );
  await context.setOffline(true);
  await page.waitForTimeout(2_000);
  await context.setOffline(false);

  await expect
    .poll(() => uploadCalls, { timeout: 120_000 })
    .toEqual(
      expect.arrayContaining([
        expect.stringMatching(
          /^200 POST \/api\/projects\/:id\/broll\/library\/multipart\/:id\/complete$/,
        ),
      ]),
    );
  expect(
    uploadCalls.filter((call) => /library\/multipart\/init$/.test(call)).length,
  ).toBe(1);
});

// Style references upload through the same shared helper and the
// style-reference resumable multipart endpoints (in the Scale panel). The Scale
// panel also lives inside the editor, which is reachable now that Task 15
// landed, so this style-reference resume journey is a passing test.
test("a style-reference upload interrupted by going offline resumes to completion", async ({
  page,
  context,
  user,
}) => {
  test.setTimeout(300_000);
  const uploadCalls = trackUploadCalls(
    page,
    /\/api\/projects\/[^/]+\/phase5\/style-profiles\/[^/]+\/references\/multipart\//,
  );

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Offline resume style reference");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await page
    .getByLabel("Choose a video file to upload")
    .setInputFiles(media.path);
  await page.getByRole("button", { name: /^Upload$/ }).click();
  await page.reload();

  await page
    .getByRole("link", { name: /editor|edit/i })
    .first()
    .click();
  await page
    .getByRole("tab", { name: /scale|style/i })
    .first()
    .click();
  await page.setInputFiles('input[type="file"][accept*="video"]', media.path);

  await expect
    .poll(() => uploadCalls, { timeout: 30_000 })
    .toEqual(
      expect.arrayContaining([
        expect.stringMatching(/references\/multipart\/init$/),
      ]),
    );
  await context.setOffline(true);
  await page.waitForTimeout(2_000);
  await context.setOffline(false);

  await expect
    .poll(() => uploadCalls, { timeout: 120_000 })
    .toEqual(
      expect.arrayContaining([
        expect.stringMatching(
          /^200 POST .*references\/multipart\/:id\/complete$/,
        ),
      ]),
    );
  expect(
    uploadCalls.filter((call) => /references\/multipart\/init$/.test(call))
      .length,
  ).toBe(1);
});
