import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../app/template.tsx", import.meta.url),
  "utf8",
);

test("the page transition uses m.div from motion/react", () => {
  assert.match(source, /from "motion\/react"/);
  assert.match(source, /<m\.div/);
  // Never the forbidden eager motion.* components.
  assert.doesNotMatch(source, /\bmotion\.[a-z]/);
});

test("the page transition carries the data-page-transition marker", () => {
  assert.match(source, /data-page-transition/);
});

test("the page transition never animates opacity (contrast safety)", () => {
  assert.doesNotMatch(source, /opacity/);
});

test("the page transition settles at transform: none", () => {
  // Either an explicit clear on completion or a rest transform of none.
  assert.match(source, /transform: "none"|transformTemplate/);
});
