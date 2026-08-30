import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { verifyProductionCandidateEvidence } from "./production-candidate-evidence.mjs";

async function writeJson(path, value) {
  await writeFile(path, `${JSON.stringify(value)}\n`);
}

async function fixture(overrides = {}) {
  const root = await mkdtemp(join(tmpdir(), "motionly-production-candidate-"));
  const externalDir = join(root, "external");
  const canaryDir = join(root, "canary");
  const { mkdir } = await import("node:fs/promises");
  await mkdir(externalDir);
  await mkdir(canaryDir);

  const external = {
    schema_version: 1,
    target: "staging-sandbox",
    run_id: "protected-run-123",
    endpoint_hosts: ["canary.motionly.test", "deploy.motionly.test"],
    ...overrides.external,
  };
  await writeJson(join(externalDir, "preflight.json"), external);
  await writeJson(join(canaryDir, "external-verification.json"), overrides.canaryExternal ?? external);
  await writeJson(join(canaryDir, "promotion.json"), {
    canary_id: "canary-123",
    previous_stable_revision: "stable-revision",
    revision: "candidate-revision",
    traffic_percent: 5,
    ...overrides.promotion,
  });
  await writeJson(join(canaryDir, "baseline.json"), {
    schema_version: 1,
    revision: "stable-revision",
    ...overrides.baseline,
  });
  for (const stage of [5, 25, 100]) {
    await writeJson(join(canaryDir, `metrics-${stage}.json`), {
      schema_version: 1,
      revision: "candidate-revision",
      ...overrides[`metrics${stage}`],
    });
    await writeJson(join(canaryDir, `decision-${stage}.json`), {
      mode: "enforce",
      exit_status: 0,
      decision: { passed: true, failures: [] },
      ...overrides[`decision${stage}`],
    });
  }
  return { root, externalDir, canaryDir };
}

test("production-candidate evidence seals matching protected canary evidence", async () => {
  const evidence = await fixture();
  try {
    const result = await verifyProductionCandidateEvidence({
      revision: "candidate-revision",
      externalEvidenceDir: evidence.externalDir,
      canaryEvidenceDir: evidence.canaryDir,
    });
    assert.deepEqual(result, {
      schema_version: 1,
      revision: "candidate-revision",
      external_verification: {
        schema_version: 1,
        target: "staging-sandbox",
        run_id: "protected-run-123",
        endpoint_hosts: ["canary.motionly.test", "deploy.motionly.test"],
      },
      canary: {
        canary_id: "canary-123",
        stable_revision: "stable-revision",
        stages: [
          { stage_percent: 5, passed: true },
          { stage_percent: 25, passed: true },
          { stage_percent: 100, passed: true },
        ],
      },
    });
  } finally {
    await rm(evidence.root, { recursive: true, force: true });
  }
});

test("production-candidate evidence rejects an SLO rejection at any canary stage", async () => {
  const evidence = await fixture({
    decision25: { mode: "enforce", exit_status: 1, decision: { passed: false, failures: ["p95_regression:projects"] } },
  });
  try {
    await assert.rejects(
      verifyProductionCandidateEvidence({
        revision: "candidate-revision",
        externalEvidenceDir: evidence.externalDir,
        canaryEvidenceDir: evidence.canaryDir,
      }),
      /25% canary decision did not pass enforced SLO validation/,
    );
  } finally {
    await rm(evidence.root, { recursive: true, force: true });
  }
});

test("production-candidate evidence rejects a canary record from another protected run", async () => {
  const evidence = await fixture({
    canaryExternal: {
      schema_version: 1,
      target: "staging-sandbox",
      run_id: "different-protected-run",
      endpoint_hosts: ["canary.motionly.test", "deploy.motionly.test"],
    },
  });
  try {
    await assert.rejects(
      verifyProductionCandidateEvidence({
        revision: "candidate-revision",
        externalEvidenceDir: evidence.externalDir,
        canaryEvidenceDir: evidence.canaryDir,
      }),
      /does not match its protected preflight/,
    );
  } finally {
    await rm(evidence.root, { recursive: true, force: true });
  }
});
