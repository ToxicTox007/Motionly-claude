import assert from "node:assert/strict";
import test from "node:test";

import { attemptStoragePaths, finalizeAttemptWithCas } from "./remotion.mjs";

const scope = {
  renderJobId: "77000000-0000-4000-8000-000000000001",
  workspaceId: "33000000-0000-4000-8000-000000000001",
  projectId: "44000000-0000-4000-8000-000000000001",
};

function mockClient({ statuses, rpcResult }) {
  const calls = [];
  const removals = [];
  const client = {
    from(table) {
      assert.equal(table, "render_jobs");
      const query = {
        select() {
          return query;
        },
        eq() {
          return query;
        },
        async single() {
          calls.push("status");
          return { data: { status: statuses.shift() }, error: null };
        },
      };
      return query;
    },
    async rpc(name, parameters) {
      calls.push({ name, parameters });
      return rpcResult;
    },
    storage: {
      from(bucket) {
        assert.equal(bucket, "exports");
        return {
          async remove(paths) {
            calls.push("remove");
            removals.push(paths);
            return { error: null };
          },
        };
      },
    },
  };
  return { client, calls, removals };
}

function completionDetails() {
  return {
    benchmarkMs: 321,
    fileSizeBytes: 2048,
    width: 1080,
    height: 1920,
    fps: 30,
    durationMs: 1000,
  };
}

test("attempt paths are derived only from stable BullMQ identity and attempt", () => {
  const first = attemptStoragePaths({
    ...scope,
    queueJobId: "render:job-1",
    attemptsMade: 0,
  });
  const retry = attemptStoragePaths({
    ...scope,
    queueJobId: "render:job-1",
    attemptsMade: 1,
  });

  assert.notEqual(first.finalPath, retry.finalPath);
  assert.match(
    first.stagingPath,
    /\/attempts\/q-[A-Za-z0-9_-]+-a-1\/staging\.mp4$/,
  );
  assert.match(
    retry.finalPath,
    /\/attempts\/q-[A-Za-z0-9_-]+-a-2\/final\.mp4$/,
  );
  assert.throws(
    () =>
      attemptStoragePaths({
        ...scope,
        queueJobId: "../../client-path",
        attemptsMade: 0,
      }),
    (error) => error.code === "queue_attempt_identity_invalid",
  );
});

test("cancellation before finalization never invokes CAS and removes only this attempt", async () => {
  const attemptPaths = attemptStoragePaths({
    ...scope,
    queueJobId: "render:cancel",
    attemptsMade: 0,
  });
  const { client, calls, removals } = mockClient({
    statuses: ["canceled"],
    rpcResult: { data: null, error: null },
  });

  await assert.rejects(
    finalizeAttemptWithCas({
      client,
      ...scope,
      artifactBucket: "exports",
      attemptPaths,
      completionDetails: completionDetails(),
    }),
    (error) => error.code === "render_cancelled",
  );

  assert.deepEqual(calls, ["status", "remove"]);
  assert.deepEqual(removals, [
    [attemptPaths.stagingPath, attemptPaths.finalPath],
  ]);
});

test("CAS winner is retained and a losing attempt cleans only its own artifact", async () => {
  const attemptPaths = attemptStoragePaths({
    ...scope,
    queueJobId: "render:loser",
    attemptsMade: 1,
  });
  const { client, calls, removals } = mockClient({
    statuses: ["uploading"],
    rpcResult: {
      data: null,
      error: {
        code: "P0001",
        message: "RENDER_COMPLETION_IDEMPOTENCY_CONFLICT",
      },
    },
  });

  await assert.rejects(
    finalizeAttemptWithCas({
      client,
      ...scope,
      artifactBucket: "exports",
      attemptPaths,
      completionDetails: completionDetails(),
    }),
    /RENDER_COMPLETION_IDEMPOTENCY_CONFLICT/,
  );

  assert.equal(calls[0], "status");
  assert.equal(calls[1].name, "motionly_complete_render");
  assert.equal(calls[1].parameters.p_storage_path, attemptPaths.finalPath);
  assert.equal(calls[2], "remove");
  assert.deepEqual(removals, [
    [attemptPaths.stagingPath, attemptPaths.finalPath],
  ]);
  assert.ok(!removals.flat().some((path) => path.includes("render:cancel")));
});

test("successful CAS returns its canonical result without cleanup", async () => {
  const attemptPaths = attemptStoragePaths({
    ...scope,
    queueJobId: "render:winner",
    attemptsMade: 0,
  });
  const { client, calls, removals } = mockClient({
    statuses: ["uploading"],
    rpcResult: {
      data: [
        {
          render_job_id: scope.renderJobId,
          export_id: "88000000-0000-4000-8000-000000000001",
          status: "complete",
          artifact_path: attemptPaths.finalPath,
        },
      ],
      error: null,
    },
  });

  const result = await finalizeAttemptWithCas({
    client,
    ...scope,
    artifactBucket: "exports",
    attemptPaths,
    completionDetails: completionDetails(),
  });

  assert.deepEqual(result, {
    render_job_id: scope.renderJobId,
    export_id: "88000000-0000-4000-8000-000000000001",
    status: "complete",
    artifact_path: attemptPaths.finalPath,
  });
  assert.deepEqual(
    calls.map((entry) => (typeof entry === "string" ? entry : entry.name)),
    ["status", "motionly_complete_render"],
  );
  assert.deepEqual(removals, []);
});
