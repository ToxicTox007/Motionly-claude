#!/usr/bin/env node
// `pnpm verify` — run the CPU-stage verification stages defined in stages.json.
//
//   pnpm verify                       # every stage, in order
//   pnpm verify --stage static,unit   # selected stages
//   pnpm verify --list                # show stages and their status
//
// A stage whose status is "not_implemented" is reported as such and never
// counts as passed; selecting one explicitly makes the run fail. Each step's
// output goes to test-results/verify/<stage>/<step>.log, and a summary is
// written to test-results/verify/summary.json.
import { spawn, spawnSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createWriteStream, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const resultsDir = join(root, "test-results");
const verifyDir = join(resultsDir, "verify");
const definition = JSON.parse(
  await readFile(join(root, "scripts/verify/stages.json"), "utf8"),
);
const packageJson = JSON.parse(
  await readFile(join(root, "package.json"), "utf8"),
);

const args = process.argv.slice(2);
if (args.includes("--list")) {
  for (const stage of definition.stages) {
    console.log(
      `${stage.name.padEnd(10)} ${stage.status}${stage.note ? `  — ${stage.note}` : ""}`,
    );
  }
  process.exit(0);
}
const stageArgIndex = args.indexOf("--stage");
const requested =
  stageArgIndex >= 0 && args[stageArgIndex + 1]
    ? args[stageArgIndex + 1]
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean)
    : null;
const known = new Set(definition.stages.map((stage) => stage.name));
for (const name of requested ?? []) {
  if (!known.has(name)) {
    console.error(`Unknown stage "${name}". Known: ${[...known].join(", ")}`);
    process.exit(2);
  }
}
const selected = definition.stages.filter(
  (stage) => !requested || requested.includes(stage.name),
);

const pinnedPnpm = String(packageJson.packageManager || "").replace(
  /^pnpm@/,
  "",
);
const pnpmCommand = pinnedPnpm ? `corepack pnpm@${pinnedPnpm}` : "pnpm";
const pythonCommand = resolvePython();

function resolvePython() {
  if (process.env.MOTIONLY_PYTHON) {
    const configured = process.env.MOTIONLY_PYTHON;
    // Accept a bare command name (as CI's setup-python exposes it) or a path.
    if (configured.includes("/")) return configured;
    const found = spawnSync("sh", ["-c", `command -v ${configured}`], {
      encoding: "utf8",
    });
    return found.status === 0 ? found.stdout.trim() : configured;
  }
  for (const candidate of [
    join(root, "apps/api/.venv/bin/python"),
    "/usr/bin/python3.12",
  ]) {
    if (existsSync(candidate)) return candidate;
  }
  return "python3";
}

function render(template) {
  return template
    .replaceAll("{pnpm}", pnpmCommand)
    .replaceAll("{python}", pythonCommand)
    .replaceAll("{results}", resultsDir);
}

function slug(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

async function runStep(stage, step) {
  const logPath = join(verifyDir, stage.name, `${slug(step.name)}.log`);
  await mkdir(dirname(logPath), { recursive: true });
  await mkdir(join(resultsDir, "junit"), { recursive: true });
  const command = render(step.run);
  const cwd = join(root, step.cwd || ".");
  const log = createWriteStream(logPath);
  log.write(`$ (cd ${step.cwd || "."} && ${command})\n`);
  const startedAt = Date.now();
  const exitCode = await new Promise((resolveExit) => {
    const child = spawn(command, {
      cwd,
      shell: true,
      env: {
        ...process.env,
        // Scripts that call a bare `python` must get the same interpreter.
        PATH: `${dirname(pythonCommand)}:${process.env.PATH}`,
        ...(step.env || {}),
        FORCE_COLOR: "0",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout.pipe(log, { end: false });
    child.stderr.pipe(log, { end: false });
    child.on("close", (code) => resolveExit(code ?? 1));
    child.on("error", () => resolveExit(1));
  });
  await new Promise((done) => log.end(done));
  return {
    name: step.name,
    exit_code: exitCode,
    duration_ms: Date.now() - startedAt,
    log: logPath.slice(root.length + 1),
  };
}

const summary = {
  schema_version: 1,
  started_at: new Date().toISOString(),
  requested: requested ?? "all",
  python: pythonCommand,
  pnpm: pnpmCommand,
  stages: [],
};
let failed = false;
for (const stage of selected) {
  if (stage.status !== "implemented") {
    console.log(
      `○ ${stage.name}: NOT IMPLEMENTED${stage.note ? ` (${stage.note})` : ""}`,
    );
    summary.stages.push({
      name: stage.name,
      status: "not_implemented",
      note: stage.note,
    });
    if (requested) failed = true;
    continue;
  }
  console.log(`▶ ${stage.name}`);
  const steps = [];
  let stagePassed = true;
  for (const step of stage.steps) {
    // After a failure, only cleanup steps marked "always" still run.
    if (!stagePassed && !step.always) {
      steps.push({
        name: step.name,
        exit_code: null,
        skipped_after_failure: true,
      });
      console.log(`  - ${step.name} (not run: earlier step failed)`);
      continue;
    }
    const result = await runStep(stage, step);
    steps.push(result);
    const mark = result.exit_code === 0 ? "✔" : "✖";
    console.log(
      `  ${mark} ${step.name} (${(result.duration_ms / 1000).toFixed(1)}s)`,
    );
    if (result.exit_code !== 0) {
      stagePassed = false;
      console.log(`    see ${result.log}`);
    }
  }
  summary.stages.push({
    name: stage.name,
    status: stagePassed ? "passed" : "failed",
    steps,
  });
  if (!stagePassed) failed = true;
}
summary.finished_at = new Date().toISOString();
summary.result = failed ? "failed" : "passed";
await mkdir(verifyDir, { recursive: true });
await writeFile(
  join(verifyDir, "summary.json"),
  `${JSON.stringify(summary, null, 2)}\n`,
);
const notImplemented = summary.stages.filter(
  (stage) => stage.status === "not_implemented",
);
console.log(
  `\nverify: ${summary.result.toUpperCase()}` +
    (notImplemented.length
      ? ` (${notImplemented.length} stage(s) not implemented yet: ${notImplemented.map((stage) => stage.name).join(", ")})`
      : ""),
);
process.exit(failed ? 1 : 0);
