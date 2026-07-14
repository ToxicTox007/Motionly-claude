import assert from "node:assert/strict";
import test from "node:test";

import { workerToken } from "./queue-common.mjs";

test("worker token never falls back to the admin token", () => {
  const originalWorker = process.env.WORKER_API_TOKEN;
  const originalAdmin = process.env.ADMIN_API_TOKEN;
  try {
    delete process.env.WORKER_API_TOKEN;
    process.env.ADMIN_API_TOKEN = "admin-only-token";
    assert.equal(workerToken(), "");

    process.env.WORKER_API_TOKEN = "worker-only-token";
    assert.equal(workerToken(), "worker-only-token");
  } finally {
    if (originalWorker === undefined) delete process.env.WORKER_API_TOKEN;
    else process.env.WORKER_API_TOKEN = originalWorker;
    if (originalAdmin === undefined) delete process.env.ADMIN_API_TOKEN;
    else process.env.ADMIN_API_TOKEN = originalAdmin;
  }
});
