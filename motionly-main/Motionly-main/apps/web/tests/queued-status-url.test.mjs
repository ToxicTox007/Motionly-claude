import assert from "node:assert/strict";
import test from "node:test";

import { normalizeQueuedLayerStatusUrl } from "../lib/queued-status-url.mjs";

test("queued layer paths resolve through the browser /api proxy", () => {
  assert.equal(
    normalizeQueuedLayerStatusUrl("/projects/project-1/jobs/job-1", "/api"),
    "/api/projects/project-1/jobs/job-1",
  );
  assert.equal(
    normalizeQueuedLayerStatusUrl(
      "projects/project-1/jobs/job-1?wait=1",
      "/api/",
    ),
    "/api/projects/project-1/jobs/job-1?wait=1",
  );
});

test("queued layer paths resolve against absolute API bases", () => {
  assert.equal(
    normalizeQueuedLayerStatusUrl(
      "/projects/project-1/jobs/job-1?wait=1",
      "https://api.example.test/v1/",
    ),
    "https://api.example.test/v1/projects/project-1/jobs/job-1?wait=1",
  );
  assert.equal(
    normalizeQueuedLayerStatusUrl(
      "https://api.example.test/projects/project-1/jobs/job-1",
      "https://api.example.test/v1",
    ),
    "https://api.example.test/projects/project-1/jobs/job-1",
  );
});

test("queued layer status URL rejects malformed and cross-origin targets", () => {
  assert.throws(
    () => normalizeQueuedLayerStatusUrl("https://evil.example/jobs/1", "/api"),
    /absolute API base/,
  );
  assert.throws(
    () =>
      normalizeQueuedLayerStatusUrl(
        "https://evil.example/jobs/1",
        "https://api.example.test",
      ),
    /same API origin/,
  );
  assert.throws(
    () => normalizeQueuedLayerStatusUrl("//evil.example/jobs/1", "/api"),
    /protocol-relative/,
  );
  assert.throws(
    () => normalizeQueuedLayerStatusUrl("", "/api"),
    /must not be empty/,
  );
});
