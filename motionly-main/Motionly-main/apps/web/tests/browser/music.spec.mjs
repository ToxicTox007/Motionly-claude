// Task 28: in the editor's Audio tab the user picks a catalog track, previews
// it (a short-lived signed link from the private music-catalog bucket), and the
// render uses it; then the finished export is downloaded.
//
// D3 forbids importing any real track until the user signs off, so the catalog
// is empty on the shared box. To exercise the real AudioTab React component —
// its mood-filtered alternatives, the pick → select POST, and the preview →
// signed-link → <audio> flow — this spec intercepts the few music endpoints
// with page.route and plays a generated test clip through the preview player.
// The render + download leg runs the real CPU pipeline (Task 15), matching
// exports-sharing.spec.mjs.
//
// Written and --list-checked here (shared 2 vCPU box); it runs at the phase
// checkpoint.
import { createSourceVideo, uploadSourceVideo } from "./support/media.mjs";
import { expect, signIn, test } from "./support/fixtures.mjs";

// A generated test track served as the preview payload. base64 WAV (silence is
// fine for the preview element; the render leg uses the real pipeline).
const TEST_TRACK = {
  id: "11111111-1111-4111-8111-111111111111",
  workspace_id: "22222222-2222-4222-8222-222222222222",
  project_id: "33333333-3333-4333-8333-333333333333",
  provider: "licensed_catalog",
  provider_track_id: "test-track-uplifting",
  title: "Test track: Uplifting Bed",
  license: "CC0-1.0",
  duration_ms: 60000,
  mood_tags: ["uplifting", "inspiring"],
  bpm: 120,
  energy: 0.7,
  vocals: false,
  loopable: true,
  beat_map: [0, 500, 1000, 1500],
  raw_metadata: {
    audio_bucket: "music-catalog",
    audio_path: "test/uplifting.wav",
  },
  created_at: new Date().toISOString(),
};

function jsonRoute(body, status = 200) {
  return (route) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
}

let media;

test.beforeAll(() => {
  media = createSourceVideo();
});

test.afterAll(() => media?.remove());

test("Audio tab: pick a track, preview it, render, and download", async ({
  page,
  user,
}) => {
  test.setTimeout(300_000);

  // Mood-filtered alternatives come from the catalog (synonym search, N9).
  await page.route(
    /\/api\/projects\/[^/]+\/music\/tracks(\?.*)?$/,
    jsonRoute({ tracks: [TEST_TRACK], mood: "uplifting ambient luxury" }),
  );
  // Preview mints a short-lived signed link from the private bucket.
  await page.route(
    /\/api\/projects\/[^/]+\/music\/tracks\/[^/]+\/preview(\?.*)?$/,
    jsonRoute({
      track_id: TEST_TRACK.id,
      provider_track_id: TEST_TRACK.provider_track_id,
      title: TEST_TRACK.title,
      preview_url:
        "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=",
      expires_in_seconds: 300,
    }),
  );
  // The select POST records the chosen track for the render.
  let selectedTrackId = null;
  await page.route(/\/api\/projects\/[^/]+\/music\/select(\?.*)?$/, (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    selectedTrackId = body.music_track_id ?? selectedTrackId;
    return jsonRoute({
      selection: {
        ...TEST_TRACK,
        music_track_id: TEST_TRACK.id,
        music_volume: 0.25,
        sfx_intensity: 0.2,
        rights_confirmed: true,
        status: "ready",
        music_track: TEST_TRACK,
        regions: [],
      },
      status: "ready",
    })(route);
  });

  // 1. Create a project and upload footage.
  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Music selection journey");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await uploadSourceVideo(page, media.path);

  // 2. Reach the editor.
  await expect(async () => {
    await page.reload();
    await expect(page.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 240_000, intervals: [10_000] });

  // 3. Open the Audio tab.
  await page.getByRole("tab", { name: /^Audio$/ }).click();
  await expect(page.getByRole("heading", { name: /^Audio$/ })).toBeVisible();

  // 4. Pick the track alternative from the catalog.
  const pickButton = page
    .getByRole("button", { name: new RegExp(TEST_TRACK.title, "i") })
    .first();
  await pickButton.click();
  await expect(
    page.getByText(new RegExp(`Selected ${TEST_TRACK.title}`, "i")),
  ).toBeVisible();
  expect(selectedTrackId).toBe(TEST_TRACK.id);

  // 5. Preview the track: a signed link plays in the preview <audio> element.
  await page
    .getByRole("button", {
      name: new RegExp(`Preview ${TEST_TRACK.title}`, "i"),
    })
    .click();
  const previewPlayer = page.getByLabel(
    new RegExp(`Preview player for ${TEST_TRACK.title}`, "i"),
  );
  await expect(previewPlayer).toBeVisible();
  await expect(previewPlayer).toHaveAttribute("src", /^data:audio\/wav/);

  // 6. Render the export using the chosen track (real CPU pipeline).
  await page.getByRole("button", { name: /^Start Render$/ }).click();
  await expect(page.getByText(/Export is ready to download/i)).toBeVisible({
    timeout: 240_000,
  });

  // 7. Download the finished export.
  await page.getByRole("button", { name: /Load player for v1/i }).click();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /Download export version 1/i }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/-v1\.mp4$/);
});
