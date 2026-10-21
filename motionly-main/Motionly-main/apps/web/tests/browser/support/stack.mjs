// Reads the running CPU-stage stack's generated credentials. The stack checker
// (scripts/verify/cpu_stage_stack.py up) writes test-results/cpu-stage/state.json,
// which names the per-run env file.
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const DEFAULT_STATE = resolve(
  here,
  "../../../../../test-results/cpu-stage/state.json",
);

export function stackState() {
  const path = process.env.MOTIONLY_CPU_STAGE_STATE || DEFAULT_STATE;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    throw new Error(
      `The browser suite runs against the CPU stage; start it first (${path}: ${error.message}).`,
    );
  }
}

/**
 * Read one of the stack's generated Docker secrets by name from the run's
 * secrets_root (test-results/cpu-stage/state.json names it). Used for secrets
 * that are not surfaced in the compose env file, e.g. `merchant_api_key` for
 * the mock gateway's server-to-server API.
 */
export function stackSecret(name) {
  const { secrets_root: secretsRoot } = stackState();
  if (!secretsRoot) {
    throw new Error(`the stack state has no secrets_root; cannot read ${name}`);
  }
  return readFileSync(join(secretsRoot, "secrets", name), "utf8").trim();
}

export function stackCredentials() {
  const env = Object.fromEntries(
    readFileSync(stackState().env_file, "utf8")
      .split("\n")
      .filter((line) => line.includes("="))
      .map((line) => [
        line.slice(0, line.indexOf("=")),
        line.slice(line.indexOf("=") + 1),
      ]),
  );
  return {
    serviceRoleKey: env.SUPABASE_SERVICE_ROLE_KEY,
    anonKey: env.SUPABASE_ANON_KEY,
    // The mock gateway's server-to-server merchant API key lives only in the
    // secrets dir (never the compose env), so it is read lazily on demand.
    get merchantApiKey() {
      return stackSecret("merchant_api_key");
    },
  };
}
