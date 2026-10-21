import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  assertQueuePayloadSafe,
  bullTransportAttempts,
  normalizeJobRecord,
  workflowJobFields,
} from "./queue-common.mjs";
import {
  canonicalizeWorkflowResult,
  deterministicWorkflowAttemptKey,
  domainJobPayload,
  normalizeWorkflowErrorCode,
  workflowBeginParameters,
  workflowRetryable,
  runJob,
} from "./worker.mjs";

const IDS = Object.freeze({
  workspace: "37000000-0000-4000-8000-000000000001",
  project: "47000000-0000-4000-8000-000000000001",
  run: "57000000-0000-4000-8000-000000000001",
  step: "67000000-0000-4000-8000-000000000001",
});

function workflowJob(overrides = {}) {
  const progress = [];
  const { data: dataOverrides = {}, ...jobOverrides } = overrides;
  const job = {
    id: "queue-job-0001",
    queueName: "visual_analysis",
    timestamp: Date.now() - 100,
    attemptsMade: 0,
    opts: { attempts: 1 },
    data: {
      id: "queue-job-0001",
      kind: "visual_analysis",
      queue_name: "visual_analysis",
      workspace_id: IDS.workspace,
      project_id: IDS.project,
      resource_id: "77000000-0000-4000-8000-000000000001",
      workflow_run_id: IDS.run,
      workflow_step_id: IDS.step,
      step_key: "analyze-source-v1",
      step_kind: "analyze_source",
      attempt_key: "dispatcher-attempt-0001",
      workflow_attempt_number: 1,
      workflow_max_attempts: 3,
      input_hash: "a".repeat(64),
      next_run_state: "proposal_ready",
      ...dataOverrides,
    },
    async updateProgress(value) {
      progress.push(value);
    },
    discard() {
      job.discarded = true;
    },
    ...jobOverrides,
  };
  job.progress = progress;
  return job;
}

function activeBeginResult() {
  return {
    step: { id: IDS.step, state: "running" },
    attempt: { state: "active", claim_token: "claim-token-0001" },
    deduplicated: false,
    exhausted: false,
  };
}

const noSlo = async () => {};

test("workflow identifiers propagate through queue records and shipped bridges stay identical", async () => {
  const payload = {
    workflow_run_id: IDS.run,
    workflow_step_id: IDS.step,
    step_key: "analyze-source-v1",
    step_kind: "analyze_source",
    attempt_key: "dispatcher-attempt-0001",
    workflow_attempt_number: 1,
    workflow_max_attempts: 3,
    input_hash: "b".repeat(64),
    next_run_state: "proposal_ready",
  };
  assert.deepEqual(workflowJobFields(payload), payload);

  const record = normalizeJobRecord(
    {
      id: "queue-job-0001",
      name: "visual_analysis",
      queueName: "visual_analysis",
      opts: { attempts: 1 },
      data: { ...payload, kind: "visual_analysis" },
    },
    "waiting",
  );
  for (const [key, value] of Object.entries(payload))
    assert.equal(record[key], value);
  assert.equal(record.max_attempts, 1);
  assert.equal(record.workflow_max_attempts, 3);
  assert.equal(bullTransportAttempts({ ...payload, max_attempts: 3 }), 1);
  assert.equal(bullTransportAttempts({ max_attempts: 3 }), 3);

  const workerCommon = await readFile(
    new URL("./queue-common.mjs", import.meta.url),
    "utf8",
  );
  const workerBridge = await readFile(
    new URL("./queue-bridge.mjs", import.meta.url),
    "utf8",
  );
  // Single source of truth: apps/worker/src is the only on-disk copy. The API
  // control-plane bridge is produced from it by the Dockerfiles at build time
  // (Task 32 removed the apps/api/worker-bridge/*.mjs duplicates), so the two
  // can never drift. Assert the image build copies exactly this source.
  for (const dockerfile of ["../../../Dockerfile", "../../api/Dockerfile"]) {
    const text = await readFile(new URL(dockerfile, import.meta.url), "utf8");
    assert.match(
      text,
      /COPY apps\/worker\/src\/queue-common\.mjs \.\/apps\/api\/worker-bridge\/queue-common\.mjs/,
    );
    assert.match(
      text,
      /COPY apps\/worker\/src\/queue-bridge\.mjs \.\/apps\/api\/worker-bridge\/queue-bridge\.mjs/,
    );
  }
  assert.match(workerBridge, /jobId: payload\.id/);
  assert.ok(workerCommon.length > 0);
});

test("queue payloads reject credentials, signed URLs, and binary media", () => {
  assert.throws(
    () =>
      workflowJobFields({
        workflow_run_id: IDS.run,
        service_role_key: "secret",
      }),
    /forbidden/,
  );
  assert.throws(
    () =>
      assertQueuePayloadSafe({
        output: "https://store.test/a?X-Amz-Signature=secret",
      }),
    /signed URLs/,
  );
  assert.throws(
    () => assertQueuePayloadSafe({ payload: new Uint8Array([1, 2, 3]) }),
    /binary data/,
  );
  assert.throws(
    () => workflowJobFields({ workflow_attempt_number: 1.5 }),
    /integer between 1 and 20/,
  );
});

test("Temporal attempt keys stay canonical across Bull re-execution", () => {
  const first = workflowJob();
  const replay = workflowJob();
  const stalledReplay = workflowJob({ attemptsMade: 1 });
  const nextTemporalAttempt = workflowJob({
    data: {
      attempt_key: "dispatcher-attempt-0002",
      workflow_attempt_number: 2,
    },
  });

  assert.equal(
    deterministicWorkflowAttemptKey(first),
    "dispatcher-attempt-0001",
  );
  assert.equal(
    deterministicWorkflowAttemptKey(first),
    deterministicWorkflowAttemptKey(replay),
  );
  assert.equal(
    deterministicWorkflowAttemptKey(first),
    deterministicWorkflowAttemptKey(stalledReplay),
  );
  assert.notEqual(
    deterministicWorkflowAttemptKey(first),
    deterministicWorkflowAttemptKey(nextTemporalAttempt),
  );

  const left = canonicalizeWorkflowResult({
    z: 2,
    nested: { b: true, a: "x" },
  });
  const right = canonicalizeWorkflowResult({
    nested: { a: "x", b: true },
    z: 2,
  });
  assert.equal(left.canonical, '{"nested":{"a":"x","b":true},"z":2}');
  assert.equal(left.hash, right.hash);
  assert.deepEqual(left.value, right.value);
  const sparse = [];
  sparse[1] = "unsafe";
  assert.throws(
    () => canonicalizeWorkflowResult({ sparse }),
    (error) => error.code === "workflow_result_unsafe",
  );
});

test("workflow transport uses one Bull attempt while Temporal owns retries", () => {
  const first = workflowJob();
  const second = workflowJob({
    data: {
      attempt_key: "dispatcher-attempt-0002",
      workflow_attempt_number: 2,
    },
  });
  const third = workflowJob({
    data: {
      attempt_key: "dispatcher-attempt-0003",
      workflow_attempt_number: 3,
    },
  });

  for (const job of [first, second, third]) {
    assert.equal(job.opts.attempts, 1);
    assert.equal(workflowBeginParameters(job).p_max_attempts, 3);
  }
  assert.equal(workflowRetryable(first), true);
  assert.equal(workflowRetryable(second), true);
  assert.equal(workflowRetryable(third), false);
});

test("domain jobs propagate identifier-only workflow context", () => {
  const payload = domainJobPayload(workflowJob());
  assert.deepEqual(payload, {
    job_id: "queue-job-0001",
    kind: "visual_analysis",
    queue_name: "visual_analysis",
    workspace_id: IDS.workspace,
    project_id: IDS.project,
    resource_id: "77000000-0000-4000-8000-000000000001",
    render_engine: "ffmpeg_basic",
    workflow_run_id: IDS.run,
    workflow_step_id: IDS.step,
    step_key: "analyze-source-v1",
    step_kind: "analyze_source",
    attempt_key: "dispatcher-attempt-0001",
    workflow_attempt_number: 1,
    workflow_max_attempts: 3,
    input_hash: "a".repeat(64),
    next_run_state: "proposal_ready",
  });
});

test("workflow error codes normalize to the Temporal identifier grammar", () => {
  assert.equal(normalizeWorkflowErrorCode("Error"), "error");
  assert.equal(
    normalizeWorkflowErrorCode("GenericNodeError"),
    "generic_node_error",
  );
  assert.equal(
    normalizeWorkflowErrorCode(" provider failed! "),
    "provider_failed",
  );
  assert.equal(normalizeWorkflowErrorCode("***"), "worker_error");
});

test("runJob begins, executes, and atomically completes workflow metadata", async () => {
  const job = workflowJob();
  const calls = [];
  const rpc = async (name, parameters) => {
    calls.push({ name, parameters });
    if (name === "motionly_begin_workflow_step") return activeBeginResult();
    if (name === "motionly_complete_workflow_step") {
      return {
        step: { id: IDS.step, state: "succeeded" },
        deduplicated: false,
      };
    }
    assert.fail(`unexpected RPC: ${name}`);
  };

  const result = await runJob(job, {
    executeJob: async () => ({ z: 2, status: "ready", ids: ["analysis-1"] }),
    workflowRpc: rpc,
    reportProviderSlo: noSlo,
    leaseOwner: "worker-test",
    leaseSeconds: 90,
  });

  assert.deepEqual(result, { ids: ["analysis-1"], status: "ready", z: 2 });
  assert.deepEqual(job.progress, [1, 100]);
  assert.deepEqual(
    calls.map((call) => call.name),
    ["motionly_begin_workflow_step", "motionly_complete_workflow_step"],
  );
  assert.deepEqual(calls[0].parameters, {
    p_workspace_id: IDS.workspace,
    p_project_id: IDS.project,
    p_workflow_run_id: IDS.run,
    p_step_key: "analyze-source-v1",
    p_step_kind: "analyze_source",
    p_attempt_key: deterministicWorkflowAttemptKey(job),
    p_lease_owner: "worker-test",
    p_lease_seconds: 90,
    p_input_hash: "a".repeat(64),
    p_max_attempts: 3,
  });
  const compact = canonicalizeWorkflowResult(result);
  assert.equal(calls[1].parameters.p_claim_token, "claim-token-0001");
  assert.equal(calls[1].parameters.p_output_hash, compact.hash);
  assert.deepEqual(calls[1].parameters.p_output, compact.value);
  assert.equal(calls[1].parameters.p_next_run_state, "proposal_ready");
});

test("deduplicated succeeded workflow steps return stored compact output without execution", async () => {
  const job = workflowJob();
  const stored = { status: "ready", analysis_id: "analysis-1" };
  const compact = canonicalizeWorkflowResult(stored);
  let executions = 0;
  const calls = [];

  const result = await runJob(job, {
    executeJob: async () => {
      executions += 1;
      return { status: "must-not-run" };
    },
    workflowRpc: async (name) => {
      calls.push(name);
      return {
        step: {
          id: IDS.step,
          state: "succeeded",
          output: stored,
          output_hash: compact.hash,
        },
        deduplicated: true,
        exhausted: false,
      };
    },
    reportProviderSlo: noSlo,
  });

  assert.deepEqual(result, { analysis_id: "analysis-1", status: "ready" });
  assert.equal(executions, 0);
  assert.deepEqual(calls, ["motionly_begin_workflow_step"]);
});

test("exhausted and terminal replay evidence never reaches specialized execution", async (t) => {
  await t.test("exhausted", async () => {
    const job = workflowJob();
    let executions = 0;
    const result = await runJob(job, {
      executeJob: async () => {
        executions += 1;
      },
      workflowRpc: async () => ({
        step: { id: IDS.step, state: "failed" },
        exhausted: true,
        deduplicated: false,
      }),
      reportProviderSlo: noSlo,
    });
    assert.equal(executions, 0);
    assert.equal(result.status, "workflow_step_exhausted");
  });

  await t.test("cancelled replay", async () => {
    const job = workflowJob();
    let executions = 0;
    const result = await runJob(job, {
      executeJob: async () => {
        executions += 1;
      },
      workflowRpc: async () => ({
        step: { id: IDS.step, state: "running" },
        attempt: { state: "cancelled", claim_token: "stale-token" },
        exhausted: false,
        deduplicated: true,
      }),
      reportProviderSlo: noSlo,
    });
    assert.equal(executions, 0);
    assert.equal(result.status, "workflow_step_not_claimed");
    assert.equal(result.attempt_state, "cancelled");
  });

  await t.test("expired active replay", async () => {
    const job = workflowJob();
    let executions = 0;
    await assert.rejects(
      runJob(job, {
        executeJob: async () => {
          executions += 1;
        },
        workflowRpc: async () => ({
          step: { id: IDS.step, state: "running" },
          attempt: {
            state: "active",
            claim_token: "expired-token",
            lease_expires_at: "2020-01-01T00:00:00.000Z",
          },
          exhausted: false,
          deduplicated: true,
        }),
        reportProviderSlo: noSlo,
      }),
      (error) => error.code === "workflow_stale_claim",
    );
    assert.equal(executions, 0);
  });
});

test("execution errors call fail with bounded retry state and callback errors never mask them", async () => {
  const job = workflowJob();
  const original = Object.assign(new Error("provider exploded"), {
    code: "provider_failed",
  });
  const calls = [];

  await assert.rejects(
    runJob(job, {
      executeJob: async () => {
        throw original;
      },
      workflowRpc: async (name, parameters) => {
        calls.push({ name, parameters });
        if (name === "motionly_begin_workflow_step") return activeBeginResult();
        throw Object.assign(new Error("WORKFLOW_STEP_STALE_CLAIM"), {
          code: "P0001",
        });
      },
      reportProviderSlo: noSlo,
    }),
    (error) => error === original,
  );

  assert.deepEqual(
    calls.map((call) => call.name),
    ["motionly_begin_workflow_step", "motionly_fail_workflow_step"],
  );
  assert.equal(calls[1].parameters.p_claim_token, "claim-token-0001");
  assert.equal(calls[1].parameters.p_error_code, "provider_failed");
  assert.equal(calls[1].parameters.p_error_message, "provider exploded");
  assert.equal(calls[1].parameters.p_retryable, true);
});

test("final Temporal attempt is reported non-retryable", async () => {
  const job = workflowJob({
    data: {
      attempt_key: "dispatcher-attempt-0003",
      workflow_attempt_number: 3,
    },
  });
  let failureParameters;
  await assert.rejects(
    runJob(job, {
      executeJob: async () => {
        throw new Error("final failure");
      },
      workflowRpc: async (name, parameters) => {
        if (name === "motionly_begin_workflow_step") return activeBeginResult();
        failureParameters = parameters;
        return { step: { id: IDS.step, state: "failed" }, terminal: true };
      },
      reportProviderSlo: noSlo,
    }),
    /final failure/,
  );
  assert.equal(failureParameters.p_error_code, "error");
  assert.equal(failureParameters.p_retryable, false);
});

test("unsafe or oversized successful results fail closed and fail the claimed step", async (t) => {
  await t.test("forbidden result", async () => {
    const job = workflowJob();
    const calls = [];
    await assert.rejects(
      runJob(job, {
        executeJob: async () => ({
          signed_url: "https://store.test/video.mp4?token=secret",
        }),
        workflowRpc: async (name) => {
          calls.push(name);
          if (name === "motionly_begin_workflow_step")
            return activeBeginResult();
          if (name === "motionly_fail_workflow_step") {
            return { step: { id: IDS.step, state: "queued" }, terminal: false };
          }
          assert.fail("unsafe result must not be completed");
        },
        reportProviderSlo: noSlo,
      }),
      /forbidden|signed URLs/,
    );
    assert.deepEqual(calls, [
      "motionly_begin_workflow_step",
      "motionly_fail_workflow_step",
    ]);
  });

  await t.test("oversized result", () => {
    assert.throws(
      () =>
        canonicalizeWorkflowResult(
          { metadata: "x".repeat(17_000) },
          { maxBytes: 1_024 },
        ),
      (error) => error.code === "workflow_result_oversized",
    );
  });
});

test("completion fencing failures fail the Bull job and stale fail callbacks cannot mask them", async () => {
  const job = workflowJob();
  const completionError = Object.assign(
    new Error("WORKFLOW_STEP_STALE_CLAIM"),
    {
      code: "P0001",
    },
  );
  const calls = [];

  await assert.rejects(
    runJob(job, {
      executeJob: async () => ({ status: "ready" }),
      workflowRpc: async (name) => {
        calls.push(name);
        if (name === "motionly_begin_workflow_step") return activeBeginResult();
        if (name === "motionly_complete_workflow_step") throw completionError;
        throw new Error("WORKFLOW_TERMINAL");
      },
      reportProviderSlo: noSlo,
    }),
    (error) => error === completionError,
  );

  assert.deepEqual(calls, [
    "motionly_begin_workflow_step",
    "motionly_complete_workflow_step",
    "motionly_fail_workflow_step",
  ]);
});

test("workflow RPC defaults consume bounded WORKFLOW_STEP environment values", () => {
  const originalLease = process.env.WORKFLOW_STEP_LEASE_SECONDS;
  const originalResult = process.env.WORKFLOW_STEP_RESULT_MAX_BYTES;
  process.env.WORKFLOW_STEP_LEASE_SECONDS = "1234";
  process.env.WORKFLOW_STEP_RESULT_MAX_BYTES = "2048";
  try {
    assert.equal(workflowBeginParameters(workflowJob()).p_lease_seconds, 1234);
    assert.throws(
      () => canonicalizeWorkflowResult({ metadata: "x".repeat(3000) }),
      (error) => error.code === "workflow_result_oversized",
    );
    assert.equal(
      canonicalizeWorkflowResult({ metadata: "x".repeat(1000) }).bytes < 2048,
      true,
    );
  } finally {
    if (originalLease === undefined)
      delete process.env.WORKFLOW_STEP_LEASE_SECONDS;
    else process.env.WORKFLOW_STEP_LEASE_SECONDS = originalLease;
    if (originalResult === undefined)
      delete process.env.WORKFLOW_STEP_RESULT_MAX_BYTES;
    else process.env.WORKFLOW_STEP_RESULT_MAX_BYTES = originalResult;
  }
});

test("workflow render_draft normalizes FFmpeg and Remotion results before fenced completion", async (t) => {
  const cases = [
    {
      name: "ffmpeg",
      result: {
        render_job_id: "unexpected-worker-id",
        render_status: "complete",
        failed: false,
        export_id: "88000000-0000-4000-8000-000000000001",
      },
    },
    {
      name: "remotion",
      result: {
        render_job_id: "unexpected-worker-id",
        export_id: "88000000-0000-4000-8000-000000000002",
        status: "complete",
      },
    },
  ];
  for (const testCase of cases) {
    await t.test(testCase.name, async () => {
      const renderId = "77000000-0000-4000-8000-000000000099";
      const job = workflowJob({
        queueName: testCase.name === "remotion" ? "render_remotion" : "render",
        data: {
          kind: "render",
          queue_name:
            testCase.name === "remotion" ? "render_remotion" : "render",
          resource_id: renderId,
          step_key: "render-first-draft-v2",
          step_kind: "render_draft",
          next_run_state: "draft_ready",
          render_engine:
            testCase.name === "remotion" ? "remotion_premium" : "ffmpeg_basic",
        },
      });
      const calls = [];
      const result = await runJob(job, {
        executeJob: async () => testCase.result,
        workflowRpc: async (name, parameters) => {
          calls.push({ name, parameters });
          if (name === "motionly_begin_workflow_step")
            return activeBeginResult();
          if (name === "motionly_complete_workflow_step") {
            return {
              step: { id: IDS.step, state: "succeeded" },
              deduplicated: false,
            };
          }
          assert.fail(`unexpected RPC: ${name}`);
        },
        reportProviderSlo: noSlo,
      });
      const expected = {
        failed: false,
        render_job_id: renderId,
        render_status: "complete",
      };
      assert.deepEqual(result, expected);
      assert.deepEqual(calls[1].parameters.p_output, expected);
      assert.equal(calls[1].parameters.p_next_run_state, "draft_ready");
      assert.equal(
        calls[1].parameters.p_output_hash,
        canonicalizeWorkflowResult(expected).hash,
      );
    });
  }
});

test("returned domain failures fail ordinary BullMQ jobs instead of completing them", async () => {
  const progress = [];
  const providerReports = [];
  const job = {
    id: "ordinary-domain-job",
    queueName: "matte_extraction",
    timestamp: Date.now(),
    attemptsMade: 0,
    opts: { attempts: 1 },
    data: { kind: "matte_extraction" },
    async updateProgress(value) {
      progress.push(value);
    },
    discard() {
      assert.fail(
        "a returned domain failure must use the normal failed-job path",
      );
    },
  };

  await assert.rejects(
    runJob(job, {
      executeJob: async () => ({
        failed: true,
        error_code: "matte_provider_failed",
        error_message: "Matte provider rejected the source.",
      }),
      reportProviderSlo: async (_job, success) => providerReports.push(success),
    }),
    (error) =>
      error.code === "matte_provider_failed" &&
      error.message === "Matte provider rejected the source.",
  );

  assert.deepEqual(progress, [1]);
  assert.deepEqual(providerReports, [false]);
});

test("returned workflow failures fail their claimed step without completing it", async () => {
  const job = workflowJob({
    data: {
      kind: "render",
      queue_name: "render",
      step_key: "render-first-draft-v2",
      step_kind: "render_draft",
      resource_id: "77000000-0000-4000-8000-000000000099",
    },
  });
  const calls = [];

  await assert.rejects(
    runJob(job, {
      executeJob: async () => ({
        failed: true,
        error_code: "render_provider_failed",
        error_message: "Render provider rejected the composition.",
      }),
      workflowRpc: async (name) => {
        calls.push(name);
        if (name === "motionly_begin_workflow_step") return activeBeginResult();
        if (name === "motionly_fail_workflow_step") {
          return { step: { id: IDS.step, state: "failed" }, terminal: false };
        }
        assert.fail(`failed results must not call ${name}`);
      },
      reportProviderSlo: noSlo,
    }),
    (error) => error.code === "render_provider_failed",
  );

  assert.deepEqual(calls, [
    "motionly_begin_workflow_step",
    "motionly_fail_workflow_step",
  ]);
  assert.deepEqual(job.progress, [1]);
});
