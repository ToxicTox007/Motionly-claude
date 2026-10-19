import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { runCanaryGate } from "./canary-gate.mjs";

function metrics(revision, overrides = {}) {
  return {
    schema_version: 1,
    revision,
    window_seconds: 300,
    samples: {
      http_requests: 100,
      projects: 20,
      uploads: 20,
      plans: 20,
      render_status: 20,
      ops: 20,
    },
    http: {
      error_rate: 0.001,
      status_5xx_rate: 0.001,
      p95_ms_by_surface: {
        projects: 900,
        uploads: 1200,
        plans: 1700,
        render_status: 650,
        ops: 1100,
      },
    },
    queues: { backpressure_count: 0, max_oldest_queued_seconds: 45 },
    providers: { error_rate: 0.002, p95_latency_ms: 1400 },
    ...overrides,
  };
}

async function withHarness(handler, run) {
  const server = createServer(handler);
  await new Promise((resolveListen) =>
    server.listen(0, "127.0.0.1", resolveListen),
  );
  const { port } = server.address();
  try {
    return await run(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolveClose, rejectClose) =>
      server.close((error) => (error ? rejectClose(error) : resolveClose())),
    );
  }
}

function responseJson(response, value) {
  response.writeHead(200, { "content-type": "application/json" });
  response.end(JSON.stringify(value));
}

function environment(baseUrl) {
  return {
    DEPLOY_TOKEN: "test-deploy-token",
    CANARY_PROMOTE_HOOK: `${baseUrl}/promote`,
    CANARY_ROLLBACK_HOOK: `${baseUrl}/rollback`,
    CANARY_METRICS_URL: `${baseUrl}/metrics`,
    STAGING_HEALTH_URL: `${baseUrl}/health`,
  };
}

test("canary gate requires a stable baseline at every enforced promotion stage", async () => {
  const requests = [];
  const evidenceDir = await mkdtemp(join(tmpdir(), "motionly-canary-success-"));
  try {
    await withHarness(
      (request, response) => {
        const url = new URL(request.url, "http://127.0.0.1");
        requests.push({
          method: request.method,
          path: url.pathname,
          query: Object.fromEntries(url.searchParams),
        });
        if (request.method === "POST" && url.pathname === "/promote") {
          let body = "";
          request.on("data", (chunk) => {
            body += chunk;
          });
          request.on("end", () => {
            const payload = JSON.parse(body);
            responseJson(
              response,
              payload.traffic_percent === 5
                ? {
                    canary_id: "canary-1",
                    previous_stable_revision: "stable-1",
                    rollback_token: "rollback-secret",
                  }
                : { canary_id: "canary-1" },
            );
          });
          return;
        }
        if (request.method === "GET" && url.pathname === "/metrics") {
          responseJson(response, metrics(url.searchParams.get("revision")));
          return;
        }
        response.writeHead(404).end();
      },
      async (baseUrl) => {
        const sloCalls = [];
        const result = await runCanaryGate({
          revision: "candidate-1",
          environment: environment(baseUrl),
          evidenceDir,
          allowInsecureUrls: true,
          stageWaitSeconds: 0,
          runSlo: (values) => {
            sloCalls.push(values);
            return { status: 0, stdout: '{"passed":true}', stderr: "" };
          },
        });
        assert.deepEqual(result, {
          canaryId: "canary-1",
          stableRevision: "stable-1",
          stages: [5, 25, 100],
        });
        assert.equal(sloCalls.length, 3);
        assert.ok(sloCalls.every((call) => call.revision === "candidate-1"));
        assert.ok(
          sloCalls.every((call) => call.baselinePath.endsWith("baseline.json")),
        );
      },
    );

    assert.equal(requests.filter((item) => item.path === "/promote").length, 3);
    assert.deepEqual(
      requests
        .filter((item) => item.path === "/metrics")
        .map((item) => item.query.revision),
      ["stable-1", "candidate-1", "candidate-1", "candidate-1"],
    );
    const promotionEvidence = await readFile(
      join(evidenceDir, "promotion.json"),
      "utf8",
    );
    assert.doesNotMatch(promotionEvidence, /rollback-secret/);
    assert.equal(
      JSON.parse(await readFile(join(evidenceDir, "decision-100.json"), "utf8"))
        .mode,
      "enforce",
    );
  } finally {
    await rm(evidenceDir, { recursive: true, force: true });
  }
});

test("failed SLO gate rolls back exactly once and proves the stable target is healthy", async () => {
  const requests = [];
  const evidenceDir = await mkdtemp(join(tmpdir(), "motionly-canary-failure-"));
  try {
    await withHarness(
      (request, response) => {
        const url = new URL(request.url, "http://127.0.0.1");
        if (request.method === "POST") {
          let body = "";
          request.on("data", (chunk) => {
            body += chunk;
          });
          request.on("end", () => {
            requests.push({ path: url.pathname, body: JSON.parse(body) });
            if (url.pathname === "/promote") {
              responseJson(response, {
                canary_id: "canary-2",
                previous_stable_revision: "stable-2",
                rollback_token: "rollback-secret",
              });
            } else if (url.pathname === "/rollback") {
              responseJson(response, { rolled_back: true });
            } else response.writeHead(404).end();
          });
          return;
        }
        if (request.method === "GET" && url.pathname === "/metrics") {
          responseJson(response, metrics(url.searchParams.get("revision")));
          return;
        }
        if (request.method === "GET" && url.pathname === "/health") {
          requests.push({
            path: url.pathname,
            query: Object.fromEntries(url.searchParams),
          });
          responseJson(response, { revision: "stable-2", status: "ready" });
          return;
        }
        response.writeHead(404).end();
      },
      async (baseUrl) => {
        await assert.rejects(
          runCanaryGate({
            revision: "candidate-2",
            environment: environment(baseUrl),
            evidenceDir,
            allowInsecureUrls: true,
            stageWaitSeconds: 0,
            runSlo: () => ({
              status: 1,
              stdout: '{"passed":false}',
              stderr: "breach",
            }),
          }),
          /SLO enforcement rejected the 5% canary stage/,
        );
      },
    );

    const rollbacks = requests.filter((item) => item.path === "/rollback");
    assert.equal(rollbacks.length, 1);
    assert.deepEqual(rollbacks[0].body, {
      canary_id: "canary-2",
      revision: "candidate-2",
      target_revision: "stable-2",
      rollback_token: "rollback-secret",
      reason: "canary_gate_failed",
      preserve_forward_migrations: true,
    });
    assert.deepEqual(requests.find((item) => item.path === "/health").query, {
      revision: "stable-2",
    });
    const rollbackEvidence = await readFile(
      join(evidenceDir, "rollback.json"),
      "utf8",
    );
    assert.doesNotMatch(rollbackEvidence, /rollback-secret/);
    assert.deepEqual(
      JSON.parse(
        await readFile(join(evidenceDir, "rollback-health.json"), "utf8"),
      ),
      {
        healthy: true,
        checked_revision: "stable-2",
        returned_revision: "stable-2",
      },
    );
  } finally {
    await rm(evidenceDir, { recursive: true, force: true });
  }
});

test("production canary endpoints fail closed unless they use HTTPS", async () => {
  await assert.rejects(
    runCanaryGate({
      revision: "candidate-3",
      environment: {
        ...environment("http://127.0.0.1:9999"),
        EXTERNAL_VERIFICATION_AUTHORIZATION: "protected-approval-token",
        EXTERNAL_VERIFICATION_TARGET: "staging-sandbox",
        EXTERNAL_VERIFICATION_RUN_ID: "run-candidate-3",
        EXTERNAL_VERIFICATION_ALLOWED_HOSTS: "127.0.0.1",
      },
      stageWaitSeconds: 0,
    }),
    /must use HTTPS/,
  );
});

test("canary gate performs no fetch before external authorization and target checks", async () => {
  let fetchCalls = 0;
  await assert.rejects(
    runCanaryGate({
      revision: "candidate-4",
      environment: environment("https://canary.sandbox.motionly.test"),
      fetchImpl: async () => {
        fetchCalls += 1;
        throw new Error("fetch must not be called");
      },
      stageWaitSeconds: 0,
    }),
    /EXTERNAL_VERIFICATION_AUTHORIZATION/,
  );
  assert.equal(fetchCalls, 0);
});
