import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(resolve(root, relativePath), "utf8");
}

test("audio tab previews a track via a signed link from the private bucket", async () => {
  const component = await source("components/editor/tabs/audio-tab.tsx");

  // Calls the per-track preview endpoint to mint a signed link.
  assert.match(
    component,
    /\/projects\/\$\{projectId\}\/music\/tracks\/\$\{trackId\}\/preview/,
  );
  // Plays the signed URL in an audio element (the bucket is private).
  assert.match(component, /setPreviewUrl\(payload\.preview_url/);
  assert.match(component, /<audio[\s\S]*src=\{previewUrl\}/);
  assert.match(component, /aria-label=\{`Preview \$\{track\.title\}`\}/);
});

test("audio tab lets the user pick a track and persists the selection", async () => {
  const component = await source("components/editor/tabs/audio-tab.tsx");

  // Picking a track calls select with that track id.
  assert.match(component, /setSelectedTrackId\(track\.id\)/);
  assert.match(
    component,
    /updateAudio\(\{ trackId: track\.id, include: true \}\)/,
  );
  // The selection POSTs to the music select route.
  assert.match(component, /\/projects\/\$\{projectId\}\/music\/select/);
});

test("audio tab loads mood-filtered alternatives from the catalog", async () => {
  const component = await source("components/editor/tabs/audio-tab.tsx");
  assert.match(
    component,
    /\/projects\/\$\{projectId\}\/music\/tracks\?\$\{params\}/,
  );
  assert.match(component, /new URLSearchParams\(\{ mood \}\)/);
});
