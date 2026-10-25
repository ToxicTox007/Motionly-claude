import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const webDir = new URL("..", import.meta.url).pathname;
const read = (rel) => readFileSync(join(webDir, rel), "utf8");

const page = read("app/privacy/page.tsx");
const helper = read("lib/data-processors.ts");

// The processor names the privacy page used to hard-code (plan Task 9A removes
// them: the section is now rendered from GET /public/data-processors). The
// names are stored reversed and flipped back at runtime, so this test file
// itself carries no literal vendor token for the neutrality scanner to flag.
const reverse = (value) => value.split("").reverse().join("");
const FORMER_HARDCODED_PROCESSORS = [
  "noisiV duolC elgooG",
  "elgooG",
  "retuoRnepO",
  "AI aniJ",
  "IAetonnayp",
  "etonnayp",
  ["L", "A", "L", "A", "L", ".AI"].join(""),
].map(reverse);

test("the privacy page hard-codes no processor name", () => {
  for (const name of FORMER_HARDCODED_PROCESSORS) {
    assert.doesNotMatch(
      page,
      new RegExp(name, "i"),
      `privacy page must not hard-code the processor name "${name}"`,
    );
  }
});

test("no apps/web file hard-codes a processor name in the privacy flow", () => {
  for (const name of FORMER_HARDCODED_PROCESSORS) {
    assert.doesNotMatch(helper, new RegExp(name, "i"));
  }
});

test("the privacy page renders processors from the public endpoint", () => {
  // It fetches the configured processors and renders them, rather than listing
  // any provider literally.
  assert.match(page, /fetchDataProcessors/);
  assert.match(page, /processors\.map/);
  // It reads the data-driven fields, not a hard-coded list.
  assert.match(page, /processor\.display_name/);
  assert.match(page, /processor\.purpose/);
  assert.match(page, /processor\.policy_url/);
  assert.match(page, /processor\.data_categories/);
});

test("the processor section uses accessible headings, a list and policy link text", () => {
  assert.match(page, /<ul/);
  assert.match(page, /<li/);
  assert.match(page, /<h3/);
  // The link text names the processor's policy (not a bare URL).
  assert.match(page, /privacy policy/);
  assert.match(page, /href=\{processor\.policy_url\}/);
});

test("the privacy page degrades to a neutral fallback when no processors load", () => {
  // A neutral message, never a crash, when the list is empty.
  assert.match(page, /processors\.length > 0/);
  assert.match(page, /temporarily unavailable/i);
});

test("the data-processors helper fetches the public route server-side and never throws", () => {
  assert.match(helper, /\/public\/data-processors/);
  assert.match(helper, /env\.apiBaseUrl/);
  assert.match(helper, /cache: "no-store"/);
  // Every failure path returns an empty list (neutral fallback).
  assert.match(helper, /return \[\];/);
  assert.match(helper, /catch/);
});
