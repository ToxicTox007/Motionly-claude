// Text colours meet WCAG 2.1 AA contrast (4.5:1) on every surface, in every
// theme. The browser suite checks rendered pages with axe; this checks the
// design tokens, so pages the suite does not visit are covered too.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const css = readFileSync(
  new URL("../app/globals.css", import.meta.url),
  "utf8",
);
const tailwind = readFileSync(
  new URL("../tailwind.config.ts", import.meta.url),
  "utf8",
);

function themeTokens(selector) {
  const start = css.indexOf(`${selector} {`);
  assert.notEqual(start, -1, `theme block ${selector} exists`);
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

const THEMES = {
  dark: themeTokens(":root"),
  light: themeTokens(':root[data-theme="light"]'),
  "system light": themeTokens(':root:not([data-theme="dark"])'),
};
const TEXT_TOKENS = [
  "foreground",
  "muted-foreground",
  "primary-text",
  "danger-text",
  "warning-text",
  "success-text",
  "info-text",
  "accent-text",
];
// Text drawn on a filled colour, e.g. a danger button.
const FILL_PAIRS = [
  ["primary-foreground", "primary"],
  ["accent-foreground", "accent"],
  ["danger-foreground", "danger"],
];
const SURFACES = ["background", "surface", "surface-elevated", "muted"];

for (const [theme, tokens] of Object.entries(THEMES)) {
  test(`${theme} theme: text colours reach 4.5:1 on every surface`, () => {
    const failures = [];
    for (const text of TEXT_TOKENS) {
      assert.ok(tokens[text], `${theme} defines --${text}`);
      for (const surface of SURFACES) {
        const ratio = contrast(tokens[text], tokens[surface]);
        if (ratio < 4.5)
          failures.push(`--${text} on --${surface}: ${ratio.toFixed(2)}`);
      }
    }
    for (const [text, fill] of FILL_PAIRS) {
      const ratio = contrast(tokens[text], tokens[fill]);
      if (ratio < 4.5)
        failures.push(`--${text} on --${fill}: ${ratio.toFixed(2)}`);
    }
    assert.deepEqual(failures, []);
  });
}

test("brand and status text utilities use their text-safe colours; fills keep the brand colours", () => {
  const textColor = tailwind.slice(
    tailwind.indexOf("textColor:"),
    tailwind.indexOf("}", tailwind.indexOf("textColor:")),
  );
  for (const name of [
    "primary",
    "danger",
    "warning",
    "success",
    "info",
    "accent",
  ]) {
    assert.match(
      textColor,
      new RegExp(`\\b${name}: "hsl\\(var\\(--${name}-text\\)\\)"`),
      name,
    );
  }
  assert.match(tailwind, /\bprimary:\s*"hsl\(var\(--primary\)\)"/);
});

test("muted, brand and status text are never faded with an opacity modifier", () => {
  const offenders = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.tsx?$/.test(name)) {
        const source = readFileSync(path, "utf8");
        for (const match of source.matchAll(
          /\btext-(muted-foreground|primary|danger|warning|success|info|accent)\/\d+/g,
        )) {
          offenders.push(`${path}: ${match[0]}`);
        }
      }
    }
  };
  walk(new URL("../app", import.meta.url).pathname);
  walk(new URL("../components", import.meta.url).pathname);
  assert.deepEqual(offenders, []);
});
