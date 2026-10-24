/**
 * Environments for child processes that handle untrusted media.
 *
 * The worker process holds the Supabase service/worker key, the worker API
 * token, the Redis URL (with its password) and provider credentials. Remotion
 * launches Chromium with `--no-sandbox --single-process` (it always does, see
 * @remotion/renderer open-browser.js), and ffmpeg parses user-supplied media,
 * so a compromise of either would otherwise be able to read every secret from
 * its environment. These helpers forward only what those tools need: an
 * allowlist, so a newly added secret is never forwarded by accident.
 */

// What any media tool needs to run, find fonts and write temp files.
const BASE_ENV_NAMES = [
  "PATH",
  "HOME",
  "TMPDIR",
  "TMP",
  "TEMP",
  "LANG",
  "LANGUAGE",
  "TZ",
  "FONTCONFIG_PATH",
  "FONTCONFIG_FILE",
  "XDG_CACHE_HOME",
  "XDG_CONFIG_HOME",
  "XDG_DATA_HOME",
  "XDG_RUNTIME_DIR",
  "LD_LIBRARY_PATH",
];
const BASE_ENV_PREFIXES = ["LC_"];

// The Remotion render CLI: Node, ts-node, the system Chromium, and the two
// environment switches @remotion/renderer itself reads.
const RENDERER_ENV_NAMES = [
  ...BASE_ENV_NAMES,
  "NODE_ENV",
  "NODE_OPTIONS",
  "BROWSER_EXECUTABLE_PATH",
  "PUPPETEER_EXECUTABLE_PATH",
  "REMOTION_PARTITIONED_RENDERING",
  "DISABLE_FROM_SURFACE",
];
const RENDERER_ENV_PREFIXES = [...BASE_ENV_PREFIXES, "TS_NODE_"];

function pick(environment, names, prefixes) {
  const allowed = new Set(names);
  const result = {};
  for (const [name, value] of Object.entries(environment)) {
    if (value === undefined) continue;
    if (
      allowed.has(name) ||
      prefixes.some((prefix) => name.startsWith(prefix))
    ) {
      result[name] = value;
    }
  }
  return result;
}

/** Environment for ffmpeg / ffprobe. */
export function mediaToolEnv(environment = process.env) {
  return pick(environment, BASE_ENV_NAMES, BASE_ENV_PREFIXES);
}

/** Environment for the Remotion render CLI (and the Chromium it launches). */
export function rendererEnv(environment = process.env, overrides = {}) {
  return {
    ...pick(environment, RENDERER_ENV_NAMES, RENDERER_ENV_PREFIXES),
    ...overrides,
  };
}
