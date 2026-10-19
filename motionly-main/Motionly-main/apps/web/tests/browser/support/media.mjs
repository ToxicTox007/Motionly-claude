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
