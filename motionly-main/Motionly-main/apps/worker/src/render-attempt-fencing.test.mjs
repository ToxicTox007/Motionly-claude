// Database audit DB-11: a render belongs to one worker run at a time. These
// tests pin the worker half; the database half is
// supabase/tests/db_audit_render_attempt_fencing_test.sql.
import assert from "node:assert/strict";
import test from "node:test";

import {
  attemptStoragePaths,
  claimRenderAttemptDirect,
  finalizeAttemptWithCas,
  persistRenderFailureDirect,
} from "./remotion.mjs";

const scope = {
  renderJobId: "77000000-0000-4000-8000-000000000001",
  workspaceId: "33000000-0000-4000-8000-000000000001",
  projectId: "44000000-0000-4000-8000-000000000001",
};
const RUN_A = "a1000000-0000-4000-8000-000000000001";
const RUN_B = "b2000000-0000-4000-8000-000000000002";

/** A Supabase client stand-in that records every filter and RPC. */
function recordingClient({
  states = [],
  rpc = { data: null, error: null },
  updated = [],
} = {}) {
  const log = { filters: [], updates: [], rpcs: [], removals: [], events: [] };
  const pending = [...states];
  const client = {
    from(table) {
      const query = {
        select() {
          return query;
        },
        update(values) {
          log.updates.push({ table, values });
          return query;
        },
        insert(values) {
          log.events.push({ table, values });
          return Promise.resolve({ error: null });
        },
        eq(column, value) {
          log.filters.push(["eq", column, value]);
          return query;
        },
        not(column, operator, value) {
          log.filters.push(["not", column, `${operator}.${value}`]);
          return query;
        },
        async single() {
          const state = pending.shift();
          assert.ok(state, "unexpected render-state lookup");
          return { data: state, error: null };
        },
        then(resolve, reject) {
          // Awaiting an update chain returns the rows it matched.
          return Promise.resolve({ data: updated, error: null }).then(
            resolve,
            reject,
          );
        },
      };
      return query;
    },
    async rpc(name, parameters) {
      log.rpcs.push({ name, parameters });
      return rpc;
    },
    storage: {
      from() {
        return {
          async remove(paths) {
            log.removals.push(paths);
            return { error: null };
          },
        };
      },
    },
  };
  return { client, log };
}

test("two live runs of one BullMQ attempt write to different files", () => {
  const identity = { ...scope, queueJobId: "render:stalled", attemptsMade: 0 };
  const first = attemptStoragePaths({ ...identity, renderAttemptId: RUN_A });
  const redelivered = attemptStoragePaths({
    ...identity,
    renderAttemptId: RUN_B,
  });

  assert.notEqual(first.finalPath, redelivered.finalPath);
  assert.equal(first.renderAttemptId, RUN_A);
  // Still inside the canonical attempts prefix the completion RPC checks.
  assert.match(
    first.finalPath,
    /\/exports\/77000000-0000-4000-8000-000000000001\/attempts\/q-[A-Za-z0-9_-]+-a-1\/final\.mp4$/,
  );
  assert.throws(
    () => attemptStoragePaths({ ...identity, renderAttemptId: "../../escape" }),
    (error) => error.code === "queue_attempt_identity_invalid",
  );
});

test("a run claims the render with its own attempt id", async () => {
  const { client, log } = recordingClient({
    rpc: {
      data: { attempt_id: RUN_A, attempt_number: 1, claimed: true },
      error: null,
    },
  });

  const claim = await claimRenderAttemptDirect(
    scope.renderJobId,
    scope.workspaceId,
    scope.projectId,
    RUN_A,
    client,
  );

  assert.equal(claim.attempt_number, 1);
  assert.equal(log.rpcs[0].name, "motionly_claim_render_attempt");
  assert.equal(log.rpcs[0].parameters.p_attempt_id, RUN_A);
});

test("a canceled or finished render cannot be claimed", async () => {
  for (const [refusal, code] of [
    ["RENDER_CANCELED", "render_cancelled"],
    ["RENDER_ALREADY_COMPLETE", "render_completion_lost"],
    ["RENDER_TERMINAL_STATE", "render_terminal_state"],
  ]) {
    const { client } = recordingClient({
      rpc: { data: null, error: { code: "P0001", message: refusal } },
    });
    await assert.rejects(
      claimRenderAttemptDirect(
        scope.renderJobId,
        scope.workspaceId,
        scope.projectId,
        RUN_A,
        client,
      ),
      (error) => error.code === code && error.retryable === false,
    );
  }
});

test("a superseded run does not publish and removes only its own files", async () => {
  const attemptPaths = attemptStoragePaths({
    ...scope,
    queueJobId: "render:superseded",
    attemptsMade: 0,
    renderAttemptId: RUN_A,
  });
  // Another run (RUN_B) took the render over while RUN_A was uploading.
  const { client, log } = recordingClient({
    states: [
      { status: "rendering", attempt_id: RUN_B },
      { status: "rendering", attempt_id: RUN_B },
    ],
  });

  await assert.rejects(
    finalizeAttemptWithCas({
      client,
      ...scope,
      artifactBucket: "exports",
      attemptPaths,
      completionDetails: {
        benchmarkMs: 1,
        fileSizeBytes: 1,
        width: 1080,
        height: 1920,
        fps: 30,
        durationMs: 1000,
      },
    }),
    (error) =>
      error.code === "render_attempt_superseded" && error.retryable === false,
  );

  assert.deepEqual(log.rpcs, [], "the completion RPC is never called");
  assert.deepEqual(log.removals, [
    [attemptPaths.stagingPath, attemptPaths.finalPath],
  ]);
});

test("the owning run publishes with its attempt id", async () => {
  const attemptPaths = attemptStoragePaths({
    ...scope,
    queueJobId: "render:owner",
    attemptsMade: 0,
    renderAttemptId: RUN_A,
  });
  const { client, log } = recordingClient({
    states: [{ status: "uploading", attempt_id: RUN_A }],
    rpc: {
      data: [
        {
          export_id: "88000000-0000-4000-8000-000000000001",
          status: "complete",
          artifact_path: attemptPaths.finalPath,
        },
      ],
      error: null,
    },
  });

  await finalizeAttemptWithCas({
    client,
    ...scope,
    artifactBucket: "exports",
    attemptPaths,
    completionDetails: {
      benchmarkMs: 1,
      fileSizeBytes: 1,
      width: 1080,
      height: 1920,
      fps: 30,
      durationMs: 1000,
    },
  });

  assert.equal(log.rpcs[0].name, "motionly_complete_render");
  assert.equal(log.rpcs[0].parameters.p_attempt_id, RUN_A);
});

test("a run's failure is written only while it owns the render", async () => {
  const job = {
    resource_id: scope.renderJobId,
    workspace_id: scope.workspaceId,
    project_id: scope.projectId,
  };
  const { client, log } = recordingClient({ updated: [] });

  const applied = await persistRenderFailureDirect(
    job,
    "boom",
    true,
    "render_worker_failed",
    RUN_A,
    client,
  );

  assert.equal(
    applied,
    false,
    "zero rows matched: another run owns the render",
  );
  assert.ok(
    log.filters.some(
      ([op, column, value]) =>
        op === "eq" && column === "attempt_id" && value === RUN_A,
    ),
  );
  assert.deepEqual(
    log.events,
    [],
    "no event is recorded for a write that did not apply",
  );
});

test("a superseded run reports no failure at all", async () => {
  const job = {
    resource_id: scope.renderJobId,
    workspace_id: scope.workspaceId,
    project_id: scope.projectId,
  };
  const { client, log } = recordingClient();

  const applied = await persistRenderFailureDirect(
    job,
    "lost",
    false,
    "render_attempt_superseded",
    RUN_A,
    client,
  );

  assert.equal(applied, false);
  assert.deepEqual(log.updates, []);
});
