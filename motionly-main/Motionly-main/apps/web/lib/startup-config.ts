import { parseServerConfig } from "./runtime-config.mjs";

const PRODUCTION_BUILD = "phase-production-build";

/**
 * Exits the server process, naming the variable, when its runtime
 * configuration is invalid (FE-42). Node.js runtime only; called from
 * instrumentation.ts as the server starts. A build needs no configuration.
 */
export function exitOnInvalidConfiguration(
  environment: NodeJS.ProcessEnv = process.env,
) {
  if (environment.NEXT_PHASE === PRODUCTION_BUILD) return;
  try {
    parseServerConfig(environment);
  } catch (error) {
    console.error(
      `Motionly web: invalid runtime configuration: ${(error as Error).message}`,
    );
    process.exit(1);
  }
}
