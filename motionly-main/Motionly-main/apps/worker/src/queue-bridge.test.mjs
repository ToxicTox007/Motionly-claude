import assert from "node:assert/strict";
import test from "node:test";

import { getJobsPaged } from "./queue-bridge.mjs";

test("getJobsPaged keeps reading past the first 500 jobs", async () => {
  const calls = [];
  const first = Array.from({ length: 500 }, (_, index) => ({ id: `job-${index}` }));
  const second = Array.from({ length: 250 }, (_, index) => ({ id: `job-${index + 500}` }));
  const queue = {
    async getJobs(states, start, end, asc) {
      calls.push({ states, start, end, asc });
      if (start === 0) return first;
      if (start === 500) return second;
      return [];
    },
  };

  const jobs = await getJobsPaged(queue, ["active"]);

  assert.equal(jobs.length, 750);
  assert.deepEqual(calls, [
    { states: ["active"], start: 0, end: 499, asc: true },
    { states: ["active"], start: 500, end: 999, asc: true },
  ]);
});
