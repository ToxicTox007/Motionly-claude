import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("VideoPreview uses real matte preview canvas and labels approximate mode", async () => {
  const source = await readFile(
    new URL("components/editor/preview/VideoPreview.tsx", root),
    "utf8",
  );

  assert.match(source, /function MattePreviewCanvas/);
  assert.match(source, /destination-in/);
  assert.match(source, /Real matte preview/);
  assert.match(source, /Approximate subject preview/);
  assert.match(source, /mattePreview\?\.status === "available"/);
});

test("Editor client threads matte preview state from visual status into preview look", async () => {
  const source = await readFile(
    new URL("components/editor-client.tsx", root),
    "utf8",
  );

  assert.match(source, /visualStatus\.matte_preview/);
  assert.match(source, /backgroundRemoval:/);
  assert.match(source, /mattePreview:/);
});
