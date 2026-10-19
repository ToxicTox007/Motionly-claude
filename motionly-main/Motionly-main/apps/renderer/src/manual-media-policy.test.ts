import assert from "node:assert/strict";
import test from "node:test";

import {
  manualVisualBackground,
  safeRendererBackground,
  safeRendererMediaUrl,
} from "./manual-media-policy.js";

test("renderer accepts only the worker loopback media proxy for visual media", () => {
  assert.equal(
    safeRendererMediaUrl("http://127.0.0.1:43123/media/7"),
    "http://127.0.0.1:43123/media/7",
  );
  assert.equal(
    safeRendererMediaUrl("https://media.example.com/graphic.png"),
    null,
  );
  assert.equal(safeRendererMediaUrl("http://127.0.0.1:43123/private"), null);
  assert.equal(
    safeRendererMediaUrl("http://169.254.169.254/latest/meta-data/"),
    null,
  );
  assert.equal(
    safeRendererMediaUrl("data:audio/wav;base64,UklGRg==", {
      allowDataAudio: true,
    }),
    "data:audio/wav;base64,UklGRg==",
  );
  assert.equal(safeRendererMediaUrl("data:image/png;base64,AA=="), null);
});

test("renderer degrades CSS URL backgrounds and malformed legacy CSS to inert fallbacks", () => {
  assert.equal(
    safeRendererBackground("linear-gradient(135deg,#0f172a,#f59e0b)"),
    "linear-gradient(135deg,#0f172a,#f59e0b)",
  );
  assert.equal(
    safeRendererBackground("url(http://169.254.169.254/latest/meta-data/)"),
    "transparent",
  );
  assert.equal(
    safeRendererBackground('image-set("https://media.example.com/x.png")'),
    "transparent",
  );
  assert.equal(
    manualVisualBackground(
      { background: "url(https://media.example.com/x.png)" },
      "shape",
    ),
    "#14b8a6",
  );
});
