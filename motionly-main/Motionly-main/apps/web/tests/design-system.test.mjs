// Design-system guards (FE-39): one type scale and one radius/shadow scale.
// Small text uses the named sizes (text-2xs 10px, text-xxs 11px, text-xs …)
// instead of arbitrary pixel values, and Tailwind's own radius/shadow names
// that duplicated a neu-* value are not used.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

const webDir = new URL("..", import.meta.url).pathname;

function sourceFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) out.push(...sourceFiles(path));
    else if (/\.(tsx|ts|mjs)$/.test(entry) && !/\.d\.mts$/.test(entry))
      out.push(path);
  }
  return out;
}

const files = ["app", "components", "lib", "hooks"].flatMap((dir) =>
  sourceFiles(join(webDir, dir)),
);

// Deliberate exceptions, each reviewed: the hero mockup's caption chips and
// the AI prompt box sit between text-xs and text-sm on purpose.
const ARBITRARY_FONT_SIZE_ALLOWLIST = new Map([
  ["components/landing/hero-console.tsx", 2],
  ["components/editor/ai-step.tsx", 1],
]);

test("no arbitrary pixel font sizes outside the reviewed exceptions", () => {
  const offenders = [];
  for (const file of files) {
    const rel = relative(webDir, file);
    const count = (
      readFileSync(file, "utf8").match(/\btext-\[\d+(?:\.\d+)?px\]/g) ?? []
    ).length;
    if (count > (ARBITRARY_FONT_SIZE_ALLOWLIST.get(rel) ?? 0)) {
      offenders.push(`${rel}: ${count}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test("radius and shadow classes come from the neu-* scale", () => {
  const removed =
    /(?:^|[\s"'`:])(rounded|rounded-(?:sm|md|lg|xl|3xl)|shadow|shadow-(?:sm|md|lg|xl))(?=[\s"'`]|$)/gm;
  const offenders = [];
  for (const file of files) {
    for (const match of readFileSync(file, "utf8").matchAll(removed)) {
      offenders.push(`${relative(webDir, file)}: ${match[1]}`);
    }
  }
  // `const rounded = …` in plain code is not a class.
  assert.deepEqual(
    offenders.filter(
      (line) => !/lib\/(storage-gauge|model-options)/.test(line),
    ),
    [],
  );
});

test("tailwind defines the small type scale as size-only tokens", () => {
  const config = readFileSync(join(webDir, "tailwind.config.ts"), "utf8");
  assert.match(config, /"2xs": "0\.625rem"/);
  assert.match(config, /xxs: "0\.6875rem"/);
});
