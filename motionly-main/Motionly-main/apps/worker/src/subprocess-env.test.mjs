import assert from "node:assert/strict";
import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runFfmpeg, spawnRenderer } from "./remotion.mjs";
import { mediaToolEnv, rendererEnv } from "./subprocess-env.mjs";

// Secrets the worker process holds (names as deployed), plus a provider key
// that only an allowlist would catch.
const SECRETS = {
  SUPABASE_SERVICE_ROLE_KEY: "secret-service-role",
  SUPABASE_JWT_SECRET: "secret-jwt",
  WORKER_API_TOKEN: "secret-worker-token",
  REDIS_URL: "redis://:secret-redis@redis:6379/0",
  PROVIDER_API_KEY: "secret-provider",
  INFERENCE_API_KEY: "secret-inference",
  REMOTION_AWS_SECRET_ACCESS_KEY: "secret-remotion-aws",
  ADMIN_API_TOKEN: "secret-admin",
};

function withEnv(values, callback) {
  const previous = {};
  for (const [name, value] of Object.entries(values)) {
    previous[name] = process.env[name];
    process.env[name] = value;
  }
  const restore = () => {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  };
  return Promise.resolve().then(callback).finally(restore);
}

function assertNoSecrets(environment) {
  for (const [name, value] of Object.entries(SECRETS)) {
    assert.equal(environment[name], undefined, `${name} leaked`);
    assert.ok(
      !Object.values(environment).some((item) => String(item).includes(value)),
      `${name}'s value leaked under another name`,
    );
  }
}

test("renderer environment keeps runtime settings and drops secrets", () => {
  const environment = rendererEnv(
    {
      ...SECRETS,
      PATH: "/usr/bin",
      HOME: "/home/motionly",
      TMPDIR: "/tmp/motionly",
      LC_ALL: "C.UTF-8",
      NODE_ENV: "production",
      TS_NODE_TRANSPILE_ONLY: "true",
      BROWSER_EXECUTABLE_PATH: "/usr/bin/chromium",
      PUPPETEER_EXECUTABLE_PATH: "/usr/bin/chromium",
    },
    { TS_NODE_PROJECT: "/app/renderer/tsconfig.json" },
  );

  assertNoSecrets(environment);
  assert.deepEqual(environment, {
    PATH: "/usr/bin",
    HOME: "/home/motionly",
    TMPDIR: "/tmp/motionly",
    LC_ALL: "C.UTF-8",
    NODE_ENV: "production",
    TS_NODE_TRANSPILE_ONLY: "true",
    BROWSER_EXECUTABLE_PATH: "/usr/bin/chromium",
    PUPPETEER_EXECUTABLE_PATH: "/usr/bin/chromium",
    TS_NODE_PROJECT: "/app/renderer/tsconfig.json",
  });
});

test("media tool environment is narrower still", () => {
  const environment = mediaToolEnv({
    ...SECRETS,
    PATH: "/usr/bin",
    NODE_OPTIONS: "--max-old-space-size=4096",
    BROWSER_EXECUTABLE_PATH: "/usr/bin/chromium",
    FONTCONFIG_PATH: "/etc/fonts",
  });
  assertNoSecrets(environment);
  assert.deepEqual(environment, {
    PATH: "/usr/bin",
    FONTCONFIG_PATH: "/etc/fonts",
  });
});

test("the spawned render CLI does not see the worker's secrets", async () => {
  const root = await mkdtemp(join(tmpdir(), "motionly-renderer-env-"));
  try {
    // A stand-in renderer: same entry points spawnRenderer invokes, but it
    // just records the environment it was started with.
    await mkdir(join(root, "src"));
    await writeFile(join(root, "package.json"), '{"type":"module"}\n');
    await writeFile(join(root, "ts-node-register.mjs"), "\n");
    await writeFile(
      join(root, "src", "render-cli.ts"),
      [
        'import { writeFileSync } from "node:fs";',
        'const out = process.argv[process.argv.indexOf("--out") + 1];',
        "writeFileSync(out, JSON.stringify(process.env));",
        "",
      ].join("\n"),
    );
    const outputPath = join(root, "env.json");
    await withEnv({ ...SECRETS }, () =>
      spawnRenderer(join(root, "props.json"), outputPath, {
        renderJobId: "00000000-0000-4000-8000-000000000001",
        workspaceId: "00000000-0000-4000-8000-000000000002",
        projectId: "00000000-0000-4000-8000-000000000003",
        timeoutMs: 30_000,
        rendererBasePath: root,
      }),
    );
    const childEnv = JSON.parse(await readFile(outputPath, "utf8"));
    assertNoSecrets(childEnv);
    assert.equal(childEnv.PATH, process.env.PATH);
    assert.equal(childEnv.TS_NODE_PROJECT, join(root, "tsconfig.json"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("ffmpeg does not see the worker's secrets", async () => {
  const root = await mkdtemp(join(tmpdir(), "motionly-ffmpeg-env-"));
  try {
    const dump = join(root, "env.txt");
    const ffmpeg = join(root, "ffmpeg");
    await writeFile(ffmpeg, `#!/bin/sh\nenv > ${dump}\n`);
    await chmod(ffmpeg, 0o755);
    await withEnv({ ...SECRETS, PATH: `${root}:${process.env.PATH}` }, () =>
      runFfmpeg(["-version"], { timeoutMs: 5_000 }),
    );
    const childEnv = Object.fromEntries(
      (await readFile(dump, "utf8"))
        .trim()
        .split("\n")
        .map((line) => [
          line.slice(0, line.indexOf("=")),
          line.slice(line.indexOf("=") + 1),
        ]),
    );
    assertNoSecrets(childEnv);
    assert.ok(childEnv.PATH.startsWith(root));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
