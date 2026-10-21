// A short H.264/AAC source video made with ffmpeg for upload tests.
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export function createSourceVideo() {
  const directory = mkdtempSync(join(tmpdir(), "motionly-browser-media-"));
  const path = join(directory, "creator-source.mp4");
  const result = spawnSync(
    "ffmpeg",
    [
      ...["-hide_banner", "-loglevel", "error", "-y"],
      ...["-f", "lavfi", "-i", "testsrc2=size=360x640:rate=24:duration=3"],
      ...[
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100:duration=3",
      ],
      ...["-shortest", "-c:v", "libx264", "-preset", "ultrafast"],
      ...["-pix_fmt", "yuv420p", "-c:a", "aac", path],
    ],
    { encoding: "utf8" },
  );
  if (result.status !== 0) {
    rmSync(directory, { recursive: true, force: true });
    throw new Error(
      `ffmpeg could not create the source video: ${result.stderr}`,
    );
  }
  return {
    path,
    remove: () => rmSync(directory, { recursive: true, force: true }),
  };
}

/**
 * Uploads a source video through the project page and waits until the
 * resumable session is completed. Reloading before that would abort the
 * upload in the browser, so callers wait for this before polling the page.
 */
export async function uploadSourceVideo(page, path) {
  const completed = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/api\/projects\/[^/]+\/uploads\/multipart\/[^/]+\/complete$/.test(
        new URL(response.url()).pathname,
      ),
    { timeout: 60_000 },
  );
  await page.getByLabel("Choose a video file to upload").setInputFiles(path);
  await page.getByRole("button", { name: /^Upload$/ }).click();
  const response = await completed;
  if (response.status() !== 200) {
    throw new Error(`upload completion answered ${response.status()}`);
  }
}
