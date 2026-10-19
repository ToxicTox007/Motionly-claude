// Reads the running CPU-stage stack's generated credentials. The stack checker
// (scripts/verify/cpu_stage_stack.py up) writes test-results/cpu-stage/state.json,
// which names the per-run env file.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
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
  };
}
