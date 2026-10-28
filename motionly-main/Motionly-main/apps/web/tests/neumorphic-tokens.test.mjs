// Verifies the light-only neumorphic token + config contract that every other
// agent relies on. Parses globals.css the same way color-contrast.test.mjs
// does so the two stay in sync.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(
  new URL("../app/globals.css", import.meta.url),
  "utf8",
);
const tailwind = readFileSync(
  new URL("../tailwind.config.ts", import.meta.url),
  "utf8",
);
const layout = readFileSync(
  new URL("../app/layout.tsx", import.meta.url),
  "utf8",
);

// One token block (FE-39). Its selector list keeps matching the root
// element, an explicit data-theme="light" and the system light preference.
const TOKEN_SELECTOR =
  ':root,\n:root[data-theme="light"],\n:root:not([data-theme="dark"]) {';

function themeTokens() {
  const start = css.indexOf(TOKEN_SELECTOR);
  assert.notEqual(start, -1, "the token block exists");
  const block = css.slice(start, css.indexOf("}", start));
  return Object.fromEntries(
    [
      ...block.matchAll(
        /--([a-z-]+):\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%/g,
      ),
    ].map(([, name, h, s, l]) => [name, [Number(h), Number(s), Number(l)]]),
  );
}

function hslToRgb([h, s, l]) {
  s /= 100;
  l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  return [0, 8, 4].map(
    (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1))),
  );
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((v) =>
    v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [hi, lo] = [luminance(hslToRgb(a)), luminance(hslToRgb(b))].sort(
    (x, y) => y - x,
  );
  return (hi + 0.05) / (lo + 0.05);
}

const BLOCKS = { root: themeTokens() };

test("one token block covers the root, explicit light and system light", () => {
  assert.equal(css.split(TOKEN_SELECTOR).length - 1, 1);
  // No other block redefines the tokens.
  assert.equal((css.match(/--background:/g) ?? []).length, 1);
});

test("no dark theme block exists (light only)", () => {
  assert.equal(css.includes(':root[data-theme="dark"] {'), false);
});

test("the base plane is #E0E5EC (215 24% 90%)", () => {
  assert.deepEqual(BLOCKS.root["background"], [215, 24, 90]);
  assert.deepEqual(BLOCKS.root["surface"], [215, 24, 90]);
});

test("--control-border reaches at least 3:1 on background and surface", () => {
  for (const surface of ["background", "surface"]) {
    const ratio = contrast(BLOCKS.root["control-border"], BLOCKS.root[surface]);
    assert.ok(
      ratio >= 3,
      `--control-border on --${surface}: ${ratio.toFixed(2)}`,
    );
  }
});

test("--focus reaches at least 3:1 on background and surface", () => {
  for (const surface of ["background", "surface"]) {
    const ratio = contrast(BLOCKS.root["focus"], BLOCKS.root[surface]);
    assert.ok(ratio >= 3, `--focus on --${surface}: ${ratio.toFixed(2)}`);
  }
});

test("neumorphic shadow names exist in tailwind config", () => {
  for (const name of [
    "neu-raised-sm",
    "neu-raised",
    "neu-raised-lg",
    "neu-inset-sm",
    "neu-inset",
    "neu-pressed",
    "neu-glow",
  ]) {
    assert.match(tailwind, new RegExp(`"${name}":`), `shadow ${name}`);
  }
});

test("neumorphic radius names exist in tailwind config", () => {
  for (const name of [
    "neu-sm",
    "neu",
    "neu-md",
    "neu-lg",
    "neu-xl",
    "neu-2xl",
  ]) {
    assert.match(
      tailwind,
      new RegExp(`"?${name.replace(/[-]/g, "\\-")}"?:`),
      `radius ${name}`,
    );
  }
});

test("darkMode is configured as a selector", () => {
  assert.match(tailwind, /darkMode:\s*\["selector",/);
});

const fonts = readFileSync(new URL("../app/fonts.ts", import.meta.url), "utf8");

test("layout uses next/font and static light theme, with no theme script", () => {
  // The font is shared with global-error through app/fonts.ts.
  assert.match(fonts, /from "next\/font\/google"/);
  assert.match(layout, /from "\.\/fonts"/);
  assert.match(layout, /data-theme="light"/);
  assert.equal(layout.includes("themeScript"), false);
  assert.equal(layout.includes("motionly-theme"), false);
});
