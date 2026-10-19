import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

const retiredExports = [
  ["components/pixel-icons.tsx", "MotionlyMark"],
  ["components/brutal-ui.tsx", "ToastSurface"],
  ["components/brutal-ui.tsx", "SegmentedControl"],
  ["components/editor/ui.tsx", "PixelButton"],
  ["components/editor/ui.tsx", "PixelBadge"],
  ["components/editor/ui.tsx", "InspectorField"],
];

test("retired, unused UI components are no longer exported", async () => {
  for (const [path, exportName] of retiredExports) {
    const source = await readFile(new URL(path, root), "utf8");
    assert.doesNotMatch(
      source,
      new RegExp(`export (?:function|interface|const) ${exportName}\\b`),
    );
  }
});
