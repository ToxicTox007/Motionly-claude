import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { spawnRenderer } from "./remotion.mjs";

const ids = {
  renderJobId: "00000000-0000-4000-8000-000000000001",
  workspaceId: "00000000-0000-4000-8000-000000000002",
  projectId: "00000000-0000-4000-8000-000000000003",
};

/**
 * A stand-in render CLI that ignores SIGTERM and starts a long-lived
 * grandchild (like Chromium), recording both PIDs.
 */
async function withStubbornRenderer(callback) {
  const root = await mkdtemp(join(tmpdir(), "motionly-renderer-stop-"));
  try {
    await mkdir(join(root, "src"));
    await writeFile(join(root, "package.json"), '{"type":"module"}\n');
    await writeFile(join(root, "ts-node-register.mjs"), "\n");
    const pids = join(root, "pids.json");
    await writeFile(
      join(root, "src", "render-cli.ts"),
      [
        'import { spawn } from "node:child_process";',
        'import { writeFileSync } from "node:fs";',
        'process.on("SIGTERM", () => {});',
        "const browser = spawn(process.execPath, [",
        '  "-e", "process.on(\'SIGTERM\', () => {}); setInterval(() => {}, 1000)",',
        '], { stdio: "ignore" });',
        `writeFileSync(${JSON.stringify(pids)}, JSON.stringify({ cli: process.pid, browser: browser.pid }));`,
        "setInterval(() => {}, 1000);",
        "",
      ].join("\n"),
    );
    const original = process.env.RENDERER_BASE_PATH;
    process.env.RENDERER_BASE_PATH = root;
    try {
      return await callback({ root, pidsFile: pids });
    } finally {
      if (original === undefined) delete process.env.RENDERER_BASE_PATH;
      else process.env.RENDERER_BASE_PATH = original;
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function readPids(file) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      return JSON.parse(await readFile(file, "utf8"));
    } catch {
      await new Promise((done) => setTimeout(done, 20));
    }
  }
  throw new Error("renderer stub never started");
}

test(
  "a render that ignores SIGTERM is killed, with its browser, before the timeout settles",
  { skip: process.platform === "win32" },
  async () => {
    await withStubbornRenderer(async ({ root, pidsFile }) => {
      const started = Date.now();
      await assert.rejects(
        spawnRenderer(join(root, "props.json"), join(root, "out.mp4"), {
          ...ids,
          timeoutMs: 300,
          killGraceMs: 300,
          isCanceled: async () => false,
        }),
        (error) => error.code === "render_timeout" && error.retryable === true,
      );
      const elapsed = Date.now() - started;
      const { cli, browser } = await readPids(pidsFile);
      // Settled only after SIGKILL escalation, and nothing is left running.
      assert.ok(
        elapsed >= 550,
        `settled before the kill escalation (${elapsed}ms)`,
      );
      assert.equal(alive(cli), false, "render-cli survived");
      assert.equal(alive(browser), false, "the browser grandchild survived");
    });
  },
);

test(
  "a canceled render is stopped the same way",
  { skip: process.platform === "win32" },
  async () => {
    await withStubbornRenderer(async ({ root, pidsFile }) => {
      let polls = 0;
      await assert.rejects(
        spawnRenderer(join(root, "props.json"), join(root, "out.mp4"), {
          ...ids,
          timeoutMs: 30_000,
          killGraceMs: 200,
          cancelPollMs: 50,
          isCanceled: async () => {
            polls += 1;
            return polls >= 3;
          },
        }),
        (error) => error.code === "render_cancelled",
      );
      const { cli, browser } = await readPids(pidsFile);
      assert.equal(alive(cli), false);
      assert.equal(alive(browser), false);
    });
  },
);
