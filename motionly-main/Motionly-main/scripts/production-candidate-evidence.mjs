#!/usr/bin/env node

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const stages = [5, 25, 100];

/**
 * Verify the non-secret evidence emitted by the protected staging canary.
 * This intentionally performs no network or deployment operation; it only
 * seals already-collected evidence after all upstream CI gates have passed.
 */
export async function verifyProductionCandidateEvidence({
  revision,
  externalEvidenceDir = resolve(root, "test-results/external-verification"),
  canaryEvidenceDir = resolve(root, "test-results/canary"),
} = {}) {
  requireString(revision, "A deployment revision is required.");

  const external = await readJson(resolve(externalEvidenceDir, "preflight.json"), "external preflight");
  const canaryExternal = await readJson(
    resolve(canaryEvidenceDir, "external-verification.json"),
    "canary external verification",
  );
  const checkedExternal = validateExternalEvidence(external, "external preflight");
  const checkedCanaryExternal = validateExternalEvidence(
    canaryExternal,
    "canary external verification",
  );
  if (JSON.stringify(checkedExternal) !== JSON.stringify(checkedCanaryExternal)) {
    throw new Error("Canary external-verification evidence does not match its protected preflight.");
  }

  const promotion = await readJson(resolve(canaryEvidenceDir, "promotion.json"), "canary promotion");
  requireString(promotion?.canary_id, "Canary promotion is missing canary_id.");
  requireString(promotion?.previous_stable_revision, "Canary promotion is missing previous_stable_revision.");
  if (promotion.revision !== revision) {
    throw new Error("Canary promotion revision does not match the requested production candidate.");
  }
  if (promotion.previous_stable_revision === revision) {
    throw new Error("Canary promotion must retain a distinct stable rollback target.");
  }

  const baseline = await readJson(resolve(canaryEvidenceDir, "baseline.json"), "stable baseline");
  if (baseline?.revision !== promotion.previous_stable_revision) {
    throw new Error("Stable baseline revision does not match the promotion rollback target.");
  }

  const decisions = [];
  for (const stage of stages) {
    const metrics = await readJson(resolve(canaryEvidenceDir, `metrics-${stage}.json`), `${stage}% metrics`);
    if (metrics?.schema_version !== 1 || metrics?.revision !== revision) {
      throw new Error(`${stage}% canary metrics are not valid for the requested revision.`);
    }
    const decision = await readJson(resolve(canaryEvidenceDir, `decision-${stage}.json`), `${stage}% decision`);
    if (
      decision?.mode !== "enforce" ||
      decision?.exit_status !== 0 ||
      decision?.decision?.passed !== true ||
      !Array.isArray(decision?.decision?.failures) ||
      decision.decision.failures.length !== 0
    ) {
      throw new Error(`${stage}% canary decision did not pass enforced SLO validation.`);
    }
    decisions.push({ stage_percent: stage, passed: true });
  }

  return {
    schema_version: 1,
    revision,
    external_verification: checkedExternal,
    canary: {
      canary_id: promotion.canary_id,
      stable_revision: promotion.previous_stable_revision,
      stages: decisions,
    },
  };
}

function validateExternalEvidence(value, label) {
  if (value?.schema_version !== 1 || value?.target !== "staging-sandbox") {
    throw new Error(`${label} is not a staging-sandbox external-verification record.`);
  }
  requireString(value.run_id, `${label} is missing run_id.`);
  if (!Array.isArray(value.endpoint_hosts) || value.endpoint_hosts.length === 0) {
    throw new Error(`${label} is missing endpoint host evidence.`);
  }
  const endpointHosts = value.endpoint_hosts.map((host) => {
    requireString(host, `${label} contains an invalid endpoint host.`);
    return host.toLowerCase();
  });
  if (new Set(endpointHosts).size !== endpointHosts.length) {
    throw new Error(`${label} contains duplicate endpoint hosts.`);
  }
  return {
    schema_version: 1,
    target: "staging-sandbox",
    run_id: value.run_id,
    endpoint_hosts: [...endpointHosts].sort(),
  };
}

async function readJson(path, label) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    const detail = error instanceof Error ? error.message : "unknown read failure";
    throw new Error(`${label} evidence is invalid: ${detail}`);
  }
}

function requireString(value, message) {
  if (typeof value !== "string" || !value.trim()) throw new Error(message);
}

function parseArgs(values) {
  const args = {};
  for (let index = 0; index < values.length; index += 1) {
    const flag = values[index];
    if (!flag.startsWith("--")) continue;
    const value = values[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${flag} requires a value.`);
    args[flag.slice(2)] = value;
    index += 1;
  }
  return args;
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  try {
    const args = parseArgs(process.argv.slice(2));
    const output = resolve(args.output || "test-results/production-candidate/acceptance.json");
    const evidence = await verifyProductionCandidateEvidence({
      revision: args.revision,
      externalEvidenceDir: resolve(args["external-evidence-dir"] || "test-results/external-verification"),
      canaryEvidenceDir: resolve(args["canary-evidence-dir"] || "test-results/canary"),
    });
    await mkdir(dirname(output), { recursive: true });
    await writeFile(output, `${JSON.stringify(evidence, null, 2)}\n`);
    console.log(JSON.stringify(evidence, null, 2));
  } catch (error) {
    console.error(error instanceof Error ? error.message : "Production-candidate evidence verification failed.");
    process.exitCode = 1;
  }
}
