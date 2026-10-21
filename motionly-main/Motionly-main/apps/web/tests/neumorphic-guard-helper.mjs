// Neumorphic guard helper for the Motionly light-only redesign.
//
// API
// ---
//   import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";
//   assertNeumorphic(["components/button.tsx", "components/toast.tsx"]);
//
// `assertNeumorphic(relativePaths)` reads each path relative to `apps/web`
// (this file lives in `apps/web/tests`) and throws an AssertionError listing
// every forbidden legacy token it finds. Call it from inside a `node:test`
// `test(...)` body. Paths must be migrated component/page files -- do NOT pass
// `app/globals.css` or `tailwind.config.ts`, which intentionally keep the
// legacy alias definitions so un-migrated screens still render.
//
// Forbidden tokens (all mean "neo-brutalist leftovers"):
//   - `shadow-brutal*`           hard offset shadows
//   - `border-foreground`        harsh dark control border
//   - `font-black`               900-weight brutalist type
//   - `dark:` prefix             the app is light-only
//   - `shadow-[...#000...]` / `#fff` offset shadows  hardcoded colour shadows
//   - `pixel-(panel|button|input|progress|skeleton|corners|glow|ornament|grid)`
//   - `glass` / `glass-strong` / `glass-surface`     legacy surface classes
//   - `btn` / `btn-<variant>`    legacy button classes used as class tokens
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const webDir = new URL("..", import.meta.url);

/**
 * Each rule is [label, RegExp]. The regexes are written to match the token as
 * it would appear inside a className string, not as a substring of an
 * unrelated identifier.
 */
const RULES = [
  ["shadow-brutal", /\bshadow-brutal[\w-]*/g],
  ["border-foreground", /\bborder-foreground\b/g],
  ["font-black", /\bfont-black\b/g],
  ["dark: variant", /\bdark:[\w[\]#.%/-]+/g],
  ["hardcoded #000/#fff offset shadow", /shadow-\[[^\]]*#(?:000|fff)[^\]]*\]/g],
  [
    "legacy pixel-* class",
    /\bpixel-(?:panel|button|input|progress|skeleton|corners|glow|ornament|grid)[\w-]*/g,
  ],
  ["legacy glass class", /\bglass(?:-strong|-surface)?\b/g],
  // `btn` or `btn-primary` etc. as a class token (whitespace or quote bounded).
  ["legacy btn class", /(?<=["'\s])btn(?:-[a-z]+)?(?=["'\s])/g],
];

export function assertNeumorphic(relativePaths) {
  const offenders = [];
  for (const rel of relativePaths) {
    const source = readFileSync(new URL(rel, webDir), "utf8");
    for (const [label, pattern] of RULES) {
      for (const match of source.matchAll(pattern)) {
        offenders.push(`${rel}: ${label} → "${match[0]}"`);
      }
    }
  }
  assert.deepEqual(offenders, [], offenders.join("\n"));
}
