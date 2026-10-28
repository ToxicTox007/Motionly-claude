// Source-text assertions for the neumorphic landing page and hero console.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const webDir = new URL("..", import.meta.url).pathname;
const read = (rel) => readFileSync(join(webDir, rel), "utf8");

const page = read("app/page.tsx");
const hero = read("components/landing/hero-console.tsx");

test("the hero console is decorative and non-interactive", () => {
  assert.match(hero, /aria-hidden="true"/);
  // Reduced motion is read hydration-safely (behaviour:
  // tests/components/hero-showcase-hydration.test.tsx).
  assert.match(hero, /usePrefersReducedMotion\(\)/);
  assert.doesNotMatch(hero, /useReducedMotion\(\)/);
  assert.doesNotMatch(hero, /<a /);
  assert.doesNotMatch(hero, /<button/);
  assert.doesNotMatch(hero, /href=/);
  assert.doesNotMatch(hero, /tabIndex/);
});

test("the landing page keeps its CTAs, heading and approved copy", () => {
  assert.match(page, /Start free/);
  assert.match(page, /Log in/);
  assert.match(page, /<h1/);
  assert.match(page, /Soft-touch edit console/);
  assert.doesNotMatch(page, /Pixel edit console/);
  assert.doesNotMatch(page, /hard-edged/);
});

test("neither file animates opacity from an initial state", () => {
  // `initial={{ ... }}` blocks must not set opacity (text-contrast rule).
  for (const source of [page, hero]) {
    const matches = source.matchAll(/initial=\{\{([^}]*)\}\}/g);
    for (const match of matches) {
      assert.doesNotMatch(match[1], /opacity/);
    }
  }
});
