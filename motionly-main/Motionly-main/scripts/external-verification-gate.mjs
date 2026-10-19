#!/usr/bin/env node

import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const requiredTarget = "staging-sandbox";

export function verifyExternalVerification({
  environment = process.env,
  endpoints,
}) {
  const authorization = requiredValue(
    environment,
    "EXTERNAL_VERIFICATION_AUTHORIZATION",
  );
  if (authorization.length < 16) {
    throw new Error(
      "External verification authorization must come from a protected secret.",
    );
  }
  if (environment.EXTERNAL_VERIFICATION_TARGET !== requiredTarget) {
    throw new Error("External verification target must be staging-sandbox.");
  }
  const runId = requiredValue(environment, "EXTERNAL_VERIFICATION_RUN_ID");
  if (runId.length > 200)
    throw new Error("External verification run identifier is too long.");
  const allowedHosts = new Set(
    requiredValue(environment, "EXTERNAL_VERIFICATION_ALLOWED_HOSTS")
      .split(",")
      .map((host) => host.trim().toLowerCase())
      .filter(Boolean),
  );
  if (!allowedHosts.size)
    throw new Error(
      "External verification requires an endpoint hostname allowlist.",
    );
  if (!Array.isArray(endpoints) || !endpoints.length) {
    throw new Error("External verification requires at least one endpoint.");
  }

  const hosts = [];
  for (const endpoint of endpoints) {
    let url;
    try {
      url = new URL(endpoint);
    } catch {
      throw new Error(
        "External verification endpoint must be an absolute URL.",
      );
    }
    if (url.protocol !== "https:") {
      throw new Error("External verification endpoints must use HTTPS.");
    }
    if (url.username || url.password) {
      throw new Error(
        "External verification endpoints cannot contain URL credentials.",
      );
    }
    if (!allowedHosts.has(url.hostname.toLowerCase())) {
      throw new Error(
        `External verification endpoint hostname is not allowlisted: ${url.hostname}`,
      );
    }
    hosts.push(url.hostname.toLowerCase());
  }

  return {
    schema_version: 1,
    target: requiredTarget,
    run_id: runId,
    endpoint_hosts: [...new Set(hosts)].sort(),
  };
}

function requiredValue(environment, name) {
  const value = environment[name];
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(
      `Missing required external verification environment variable: ${name}`,
    );
  }
  return value.trim();
}

function parseArgs(values) {
  const endpoints = [];
  let evidenceDir = resolve(root, "test-results/external-verification");
  for (let index = 0; index < values.length; index += 1) {
    if (values[index] === "--endpoint") {
      endpoints.push(values[index + 1]);
      index += 1;
    } else if (values[index] === "--evidence-dir") {
      evidenceDir = resolve(values[index + 1]);
      index += 1;
    }
  }
  return { endpoints, evidenceDir };
}

const isMain =
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const { endpoints, evidenceDir } = parseArgs(process.argv.slice(2));
  try {
    const evidence = verifyExternalVerification({ endpoints });
    await mkdir(evidenceDir, { recursive: true });
    await writeFile(
      resolve(evidenceDir, "preflight.json"),
      `${JSON.stringify(evidence, null, 2)}\n`,
    );
    console.log(JSON.stringify(evidence));
  } catch (error) {
    console.error(
      error instanceof Error
        ? error.message
        : "External verification preflight failed.",
    );
    process.exitCode = 1;
  }
}
