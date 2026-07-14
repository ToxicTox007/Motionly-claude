import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("export UI supports captionless single and bulk renders", async () => {
  const source = await readFile(
    new URL("components/editor/export-step.tsx", root),
    "utf8",
  );

  assert.match(source, /const \[includeCaptions, setIncludeCaptions\] = useState\(Boolean\(captionTrackId\)\)/);
  assert.match(source, /aria-label="Burn captions into export"/);
  assert.match(source, /caption_track_id: includeCaptions \? \(captionTrackId \?\? null\) : null/g);
  assert.match(source, /include_captions: includeCaptions/g);
  assert.match(source, /disabled: includeCaptions && !captionTrackId/g);
  assert.match(source, /\(includeCaptions && !captionTrackId\) \|\|\s*bulkPlatforms\.length === 0/);
  assert.match(source, /\{includeCaptions && !captionTrackId \? \(/);
});
