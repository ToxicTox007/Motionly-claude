#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { verifyExternalVerification } from "./external-verification-gate.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const defaultContractPath = resolve(root, "scripts/perf/slo-contract.json");
const defaultSloScriptPath = resolve(root, "scripts/perf/assert-slo.mjs");
const terminalStates = new Set([
  "export_ready",
  "failed",
  "cancelled",
  "superseded",
]);

/**
 * Runs a staged deployment canary. The public function is dependency-injectable
 * so its rollback behavior is executable without contacting deployment systems.
 */
export async function runCanaryGate({
  revision,
  contractPath = defaultContractPath,
  evidenceDir = resolve(root, "test-results/canary"),
  environment = process.env,
  fetchImpl = globalThis.fetch,
  runSlo = defaultRunSlo,
  sleep = defaultSleep,
  stagePercentages = [5, 25, 100],
  stageWaitSeconds = readNonNegativeInteger(
    environment.CANARY_STAGE_WAIT_SECONDS,
    300,
  ),
  allowInsecureUrls = false,
} = {}) {
  if (!fetchImpl)
    throw new Error("A fetch implementation is required for the canary gate.");
  if (!isNonEmptyString(revision))
    throw new Error("A deployment revision is required.");
  const endpoints = requiredEndpoints(environment, { allowInsecureUrls });
  const verification = allowInsecureUrls
    ? null
    : verifyExternalVerification({
        environment,
        endpoints: [
          endpoints.promote,
          endpoints.rollback,
          endpoints.metrics,
          endpoints.health,
        ],
      });
  const stages = validateStages(stagePercentages);
  await mkdir(evidenceDir, { recursive: true });
  if (verification)
    await writeJson(evidenceDir, "external-verification.json", verification);

  let promotion = null;
  try {
    promotion = await startCanary({
      endpoints,
      revision,
      fetchImpl,
      evidenceDir,
    });
    const baseline = await fetchMetrics({
      url: endpoints.metrics,
      revision: promotion.stableRevision,
      fetchImpl,
    });
    if (baseline.revision !== promotion.stableRevision) {
      throw new Error(
        "Stable baseline metrics did not identify the promoted stable revision.",
      );
    }
    await writeJson(evidenceDir, "baseline.json", baseline);

    for (const [index, percentage] of stages.entries()) {
      if (index > 0) {
        await postJson({
          url: endpoints.promote,
          token: endpoints.token,
          body: {
            canary_id: promotion.canaryId,
            revision,
            traffic_percent: percentage,
          },
          fetchImpl,
          requireJson: false,
        });
        await writeJson(evidenceDir, `promote-${percentage}.json`, {
          canary_id: promotion.canaryId,
          revision,
          traffic_percent: percentage,
        });
      }

      const canary = await fetchMetrics({
        url: endpoints.metrics,
        revision,
        fetchImpl,
      });
      await writeJson(evidenceDir, `metrics-${percentage}.json`, canary);
      const decision = await runSlo({
        contractPath,
        canaryPath: resolve(evidenceDir, `metrics-${percentage}.json`),
        baselinePath: resolve(evidenceDir, "baseline.json"),
        revision,
      });
      await writeJson(evidenceDir, `decision-${percentage}.json`, {
        mode: "enforce",
        exit_status: decision.status,
        decision: parseJsonOutput(decision.stdout),
      });
      if (decision.status !== 0) {
        throw new Error(
          `SLO enforcement rejected the ${percentage}% canary stage.`,
        );
      }
      if (index < stages.length - 1 && stageWaitSeconds > 0) {
        await sleep(stageWaitSeconds * 1000);
      }
    }

    return {
      canaryId: promotion.canaryId,
      stableRevision: promotion.stableRevision,
      stages,
    };
  } catch (primaryError) {
    if (!promotion) throw primaryError;
    try {
      await rollbackAndVerify({
        endpoints,
        promotion,
        revision,
        fetchImpl,
        evidenceDir,
      });
    } catch (rollbackError) {
      await writeJson(evidenceDir, "rollback-failure.json", {
        rollback_failed: true,
        message: messageForEvidence(rollbackError),
      });
      throw new AggregateError(
        [primaryError, rollbackError],
        "Canary failed and its rollback or stable-health verification also failed.",
      );
    }
    throw primaryError;
  }
}

async function startCanary({ endpoints, revision, fetchImpl, evidenceDir }) {
  const response = await postJson({
    url: endpoints.promote,
    token: endpoints.token,
    body: { revision, traffic_percent: 5 },
    fetchImpl,
    requireJson: true,
  });
  const canaryId = requiredResponseString(response, "canary_id");
  const stableRevision = requiredResponseString(
    response,
    "previous_stable_revision",
  );
  const rollbackToken = requiredResponseString(response, "rollback_token");
  if (stableRevision === revision) {
    throw new Error(
      "Canary promotion must identify a distinct stable revision.",
    );
  }
  await writeJson(evidenceDir, "promotion.json", {
    canary_id: canaryId,
    previous_stable_revision: stableRevision,
    revision,
    traffic_percent: 5,
  });
  return { canaryId, stableRevision, rollbackToken };
}

async function rollbackAndVerify({
  endpoints,
  promotion,
  revision,
  fetchImpl,
  evidenceDir,
}) {
  await postJson({
    url: endpoints.rollback,
    token: endpoints.token,
    body: {
      canary_id: promotion.canaryId,
      revision,
      target_revision: promotion.stableRevision,
      rollback_token: promotion.rollbackToken,
      reason: "canary_gate_failed",
      preserve_forward_migrations: true,
    },
    fetchImpl,
    requireJson: false,
  });
  await writeJson(evidenceDir, "rollback.json", {
    rolled_back: true,
    canary_id: promotion.canaryId,
    revision,
    target_revision: promotion.stableRevision,
    preserve_forward_migrations: true,
  });

  const healthUrl = withQuery(endpoints.health, {
    revision: promotion.stableRevision,
  });
  const response = await fetchImpl(healthUrl, {
    headers: { authorization: `Bearer ${endpoints.token}` },
  });
  if (!response.ok) {
    throw new Error(
      `Stable revision health check failed with HTTP ${response.status}.`,
    );
  }
  const contentType = response.headers.get("content-type") || "";
  let returnedRevision = null;
  if (contentType.includes("application/json")) {
    const body = await response.json();
    if (body && typeof body === "object" && typeof body.revision === "string") {
      returnedRevision = body.revision;
      if (returnedRevision !== promotion.stableRevision) {
        throw new Error(
          "Stable health response revision did not match the rollback target.",
        );
      }
    }
  } else {
    await response.arrayBuffer();
  }
  await writeJson(evidenceDir, "rollback-health.json", {
    healthy: true,
    checked_revision: promotion.stableRevision,
    returned_revision: returnedRevision,
  });
}

async function fetchMetrics({ url, revision, fetchImpl }) {
  const response = await fetchImpl(
    withQuery(url, { revision, window_seconds: "300" }),
  );
  if (!response.ok)
    throw new Error(
      `Canary metrics request failed with HTTP ${response.status}.`,
    );
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("Canary metrics response was not valid JSON.");
  }
  if (!payload || typeof payload !== "object") {
    throw new Error("Canary metrics response must be a JSON object.");
  }
  return payload;
}

async function postJson({ url, token, body, fetchImpl, requireJson }) {
  const response = await fetchImpl(url, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(
      `Canary control request failed with HTTP ${response.status}.`,
    );
  const text = await response.text();
  if (!requireJson && !text) return null;
  try {
    return JSON.parse(text);
  } catch {
    if (requireJson)
      throw new Error("Canary promotion response was not valid JSON.");
    return null;
  }
}

function defaultRunSlo({ contractPath, canaryPath, baselinePath, revision }) {
  const result = spawnSync(
    process.execPath,
    [
      defaultSloScriptPath,
      "--contract",
      contractPath,
      "--canary",
      canaryPath,
      "--baseline",
      baselinePath,
      "--revision",
      revision,
      "--mode",
      "enforce",
    ],
    { encoding: "utf8" },
  );
  if (result.error) throw result.error;
  return {
    status: result.status ?? 1,
    stdout: result.stdout || "",
    stderr: result.stderr || "",
  };
}

function requiredEndpoints(environment, { allowInsecureUrls }) {
  const token = requiredEnvironment(environment, "DEPLOY_TOKEN");
  return {
    token,
    promote: checkedUrl(
      requiredEnvironment(environment, "CANARY_PROMOTE_HOOK"),
      allowInsecureUrls,
    ),
    rollback: checkedUrl(
      requiredEnvironment(environment, "CANARY_ROLLBACK_HOOK"),
      allowInsecureUrls,
    ),
    metrics: checkedUrl(
      requiredEnvironment(environment, "CANARY_METRICS_URL"),
      allowInsecureUrls,
    ),
    health: checkedUrl(
      requiredEnvironment(environment, "STAGING_HEALTH_URL"),
      allowInsecureUrls,
    ),
  };
}

function checkedUrl(value, allowInsecureUrls) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("Canary endpoint must be an absolute URL.");
  }
  if (parsed.username || parsed.password)
    throw new Error("Canary endpoints cannot include URL credentials.");
  if (!["https:", "http:"].includes(parsed.protocol)) {
    throw new Error("Canary endpoints must use HTTP(S).");
  }
  if (!allowInsecureUrls && parsed.protocol !== "https:") {
    throw new Error(
      "Canary endpoints must use HTTPS outside executable local tests.",
    );
  }
  return parsed.toString();
}

function validateStages(values) {
  if (
    !Array.isArray(values) ||
    values.length !== 3 ||
    values.join(",") !== "5,25,100"
  ) {
    throw new Error("Canary stages must be exactly 5, 25, and 100 percent.");
  }
  return values;
}

function requiredEnvironment(environment, name) {
  const value = environment[name];
  if (!isNonEmptyString(value))
    throw new Error(`Missing required canary environment variable: ${name}`);
  return value;
}

function requiredResponseString(response, key) {
  const value = response?.[key];
  if (!isNonEmptyString(value))
    throw new Error(`Canary promotion response requires ${key}.`);
  return value;
}

function withQuery(rawUrl, values) {
  const url = new URL(rawUrl);
  for (const [key, value] of Object.entries(values))
    url.searchParams.set(key, value);
  return url;
}

async function writeJson(directory, name, value) {
  await writeFile(
    resolve(directory, name),
    `${JSON.stringify(value, null, 2)}\n`,
  );
}

function parseJsonOutput(value) {
  try {
    return JSON.parse(value);
  } catch {
    return { parse_error: true };
  }
}

function messageForEvidence(error) {
  return error instanceof Error
    ? error.message.slice(0, 500)
    : "Unknown rollback failure.";
}

function readNonNegativeInteger(value, fallback) {
  if (value === undefined || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(
      "CANARY_STAGE_WAIT_SECONDS must be a non-negative integer.",
    );
  }
  return parsed;
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function defaultSleep(milliseconds) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, milliseconds));
}

function parseArgs(values) {
  const args = {};
  for (let index = 0; index < values.length; index += 1) {
    if (!values[index].startsWith("--")) continue;
    args[values[index].slice(2)] = values[index + 1];
    index += 1;
  }
  return args;
}

const isMain =
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const args = parseArgs(process.argv.slice(2));
  runCanaryGate({
    revision: args.revision || process.env.GITHUB_SHA,
    contractPath: args.contract || defaultContractPath,
    evidenceDir: args["evidence-dir"] || resolve(root, "test-results/canary"),
  })
    .then((result) =>
      console.log(JSON.stringify({ passed: true, ...result }, null, 2)),
    )
    .catch((error) => {
      console.error(
        error instanceof Error ? error.message : "Canary gate failed.",
      );
      process.exitCode = 1;
    });
}

export { terminalStates };
