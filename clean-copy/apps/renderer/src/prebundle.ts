/**
 * prebundle.ts
 * -----------
 * Warms the Remotion webpack bundle at container / worker startup.
 *
 * Without pre-bundling, the first render request triggers a ~30 second
 * webpack compilation. Running this script once at startup moves that cost
 * to deploy time rather than the first user's render.
 *
 * Usage (in worker startup command):
 *   node --loader ts-node/esm src/prebundle.ts && node ../worker/src/worker.mjs --loop
 *
 * The bundle path is written to .remotion-bundle so render-cli.ts can reuse
 * it on every subsequent render without re-bundling.
 */

import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { bundle } from "@remotion/bundler";

const BUNDLE_CACHE_FILE = resolve(".remotion-bundle");

async function prebundle() {
  const entryPoint = resolve("src/index.ts");

  console.log("[prebundle] Starting Remotion webpack bundle...");
  const start = Date.now();

  const bundleLocation = await bundle({ entryPoint });

  const elapsed = Math.round((Date.now() - start) / 1000);
  console.log(`[prebundle] Bundle complete in ${elapsed}s → ${bundleLocation}`);

  await writeFile(BUNDLE_CACHE_FILE, bundleLocation);
  console.log(`[prebundle] Bundle path cached at ${BUNDLE_CACHE_FILE}`);
}

prebundle().catch((error) => {
  console.error("[prebundle] Failed:", error);
  process.exitCode = 1;
});
