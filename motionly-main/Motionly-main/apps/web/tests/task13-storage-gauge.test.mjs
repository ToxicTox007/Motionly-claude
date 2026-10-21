// Task 13 / B23: the storage gauge shows the real quota and labels unknown
// quotas as "not metered" instead of fabricating a percentage.
import assert from "node:assert/strict";
import test from "node:test";

import { storageGauge, formatBytes } from "../lib/storage-gauge.mjs";

test("a metered workspace shows used of max and a clamped percentage", () => {
  const gauge = storageGauge({
    used_bytes: 2 * 1024 ** 3,
    max_bytes: 5 * 1024 ** 3,
    percentage: 40,
  });
  assert.equal(gauge.metered, true);
  assert.equal(gauge.label, "2 GB of 5 GB");
  assert.equal(gauge.percent, 40);
  assert.equal(gauge.percentLabel, "40%");
  assert.match(gauge.ariaLabel, /2 GB of 5 GB/);
});

test("percent is clamped and derived from bytes when percentage is absent", () => {
  const gauge = storageGauge({
    used_bytes: 10 * 1024 ** 3,
    max_bytes: 5 * 1024 ** 3,
  });
  assert.equal(gauge.percent, 100);
});

test("an unknown max is labelled not metered, never a fake percentage", () => {
  const gauge = storageGauge({ used_bytes: 1024 ** 3, max_bytes: 0 });
  assert.equal(gauge.metered, false);
  assert.equal(gauge.percent, null);
  assert.match(gauge.label, /not metered/);
});

test("a missing quota reports unavailable, not zero usage", () => {
  const gauge = storageGauge(null);
  assert.equal(gauge.metered, false);
  assert.equal(gauge.percent, null);
  assert.match(gauge.label, /unavailable/i);
});

test("formatBytes is human readable", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1024), "1 KB");
  assert.equal(formatBytes(5 * 1024 ** 3), "5 GB");
});
