// Render-usage meter shown in the app shell and on the billing page. The API
// reports renders_limit, remaining_renders and percent_used as null when the
// workspace is unmetered.
import assert from "node:assert/strict";
import test from "node:test";

import { usageMeter } from "../lib/usage-meter.mjs";

test("a metered workspace shows used of limit, the percentage, and what remains", () => {
  assert.deepEqual(
    usageMeter({
      renders_used: 3,
      renders_limit: 10,
      remaining_renders: 7,
      percent_used: 30,
    }),
    {
      metered: true,
      label: "3/10 renders",
      percent: 30,
      percentLabel: "30%",
      remainingLabel: "7 remaining this period.",
      ariaLabel: "Renders used this period: 3 of 10",
    },
  );
});

test("an unmetered workspace shows its usage without a limit or a percentage", () => {
  assert.deepEqual(
    usageMeter({
      renders_used: 4,
      renders_limit: null,
      remaining_renders: null,
      percent_used: null,
    }),
    {
      metered: false,
      label: "4 renders · unlimited",
      percent: null,
      percentLabel: null,
      remainingLabel: "Unlimited renders this period.",
      ariaLabel: null,
    },
  );
});

test("percentages are clamped to 0..100", () => {
  assert.equal(
    usageMeter({
      renders_used: 12,
      renders_limit: 10,
      remaining_renders: 0,
      percent_used: 120,
    }).percent,
    100,
  );
  assert.equal(
    usageMeter({
      renders_used: 0,
      renders_limit: 10,
      remaining_renders: 10,
      percent_used: -5,
    }).percent,
    0,
  );
});
