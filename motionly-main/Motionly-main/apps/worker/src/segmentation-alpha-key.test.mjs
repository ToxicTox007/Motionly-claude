import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  chmod,
  mkdtemp,
  readFile,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  applySegmentationAlphaKey,
  runFfmpeg,
  startValidatedMediaProxy,
} from "./remotion.mjs";

const hasFfmpeg =
  spawnSync("ffmpeg", ["-hide_banner", "-encoders"], {
    encoding: "utf8",
  }).stdout?.includes("libvpx-vp9") ?? false;

function ffmpegSync(args) {
  const result = spawnSync(
    "ffmpeg",
    ["-hide_banner", "-loglevel", "error", "-y", ...args],
    {
      encoding: "utf8",
    },
  );
  assert.equal(result.status, 0, result.stderr);
}

async function serveFiles(files) {
  const server = createServer(async (request, response) => {
    const body = files[request.url ?? ""];
    if (!body) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { "content-type": "video/mp4" });
    response.end(await readFile(body));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

async function withTempDir(prefix, callback) {
  const root = await mkdtemp(join(tmpdir(), prefix));
  try {
    return await callback(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

/** A fake `ffmpeg` on PATH, so timing and signal handling are deterministic. */
async function withFakeFfmpeg(script, callback) {
  return withTempDir("motionly-fake-ffmpeg-", async (bin) => {
    const path = join(bin, "ffmpeg");
    await writeFile(path, `#!/bin/sh\n${script}\n`);
    await chmod(path, 0o755);
    const original = process.env.PATH;
    process.env.PATH = `${bin}:${original}`;
    try {
      return await callback();
    } finally {
      process.env.PATH = original;
    }
  });
}

function countTicksWhile(promise) {
  let ticks = 0;
  const timer = setInterval(() => {
    ticks += 1;
  }, 10);
  return promise
    .finally(() => clearInterval(timer))
    .then(
      (value) => ({ ticks, value }),
      (error) => {
        error.ticks = ticks;
        throw error;
      },
    );
}

test(
  "background removal keys the source through the render's loopback media proxy",
  { skip: !hasFfmpeg && "ffmpeg with libvpx-vp9 is not installed" },
  async () => {
    await withTempDir("motionly-segmentation-", async (root) => {
      const source = join(root, "upstream-source.mp4");
      const matte = join(root, "upstream-matte.mp4");
      ffmpegSync([
        "-f",
        "lavfi",
        "-i",
        "testsrc=s=64x64:d=0.5:r=10",
        "-pix_fmt",
        "yuv420p",
        source,
      ]);
      ffmpegSync([
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=64x64:d=0.5:r=10",
        "-pix_fmt",
        "yuv420p",
        matte,
      ]);
      const upstream = await serveFiles({
        "/source.mp4": source,
        "/matte.mp4": matte,
      });
      // The upstream stands in for Supabase Storage (the trusted internal origin).
      const originalSupabaseUrl = process.env.SUPABASE_URL;
      process.env.SUPABASE_URL = upstream.origin;
      const proxy = await startValidatedMediaProxy();
      let keyed = null;
      try {
        const sourceUrl = proxy.register(`${upstream.origin}/source.mp4`, {
          trustedInternal: true,
        });
        const matteUrl = proxy.register(`${upstream.origin}/matte.mp4`, {
          trustedInternal: true,
        });
        assert.ok(sourceUrl.startsWith(`${proxy.origin}/media/`));

        keyed = await applySegmentationAlphaKey({
          sourceUrl,
          matteUrl,
          featherPx: 8,
          workDir: join(root, "work"),
          internalOrigin: proxy.origin,
          timeoutMs: 60_000,
        });

        const response = await fetch(keyed.url);
        assert.equal(response.status, 200);
        const keyedPath = join(root, "keyed-copy.webm");
        await writeFile(keyedPath, Buffer.from(await response.arrayBuffer()));
        const probe = spawnSync(
          "ffprobe",
          [
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name:stream_tags=alpha_mode",
            "-of",
            "json",
            keyedPath,
          ],
          { encoding: "utf8" },
        );
        const [stream] = JSON.parse(probe.stdout).streams;
        assert.equal(stream.codec_name, "vp9");
        assert.equal(stream.tags?.alpha_mode ?? stream.tags?.ALPHA_MODE, "1");
      } finally {
        if (keyed) await keyed.close();
        await proxy.close();
        await upstream.close();
        if (originalSupabaseUrl === undefined) delete process.env.SUPABASE_URL;
        else process.env.SUPABASE_URL = originalSupabaseUrl;
      }
    });
  },
);

test("background removal still refuses loopback URLs that are not the proxy", async () => {
  await withTempDir("motionly-segmentation-ssrf-", async (root) => {
    const other = await serveFiles({ "/secret": join(root, "missing") });
    const proxy = await startValidatedMediaProxy();
    try {
      await assert.rejects(
        applySegmentationAlphaKey({
          sourceUrl: `${other.origin}/secret`,
          matteUrl: `${other.origin}/secret`,
          workDir: join(root, "work"),
          internalOrigin: proxy.origin,
          timeoutMs: 5_000,
        }),
        /not publicly routable/,
      );
    } finally {
      await proxy.close();
      await other.close();
    }
  });
});

test("ffmpeg runs without blocking the event loop", async () => {
  await withFakeFfmpeg("sleep 0.4", async () => {
    const { ticks } = await countTicksWhile(
      runFfmpeg(["-version"], { timeoutMs: 5_000 }),
    );
    // A synchronous spawn would allow ~0 ticks; a free loop gets ~40.
    assert.ok(ticks >= 15, `event loop ticked only ${ticks} times`);
  });
});

test("a hung ffmpeg is killed after the timeout and its grace period", async () => {
  await withTempDir("motionly-ffmpeg-pid-", async (root) => {
    const pidFile = join(root, "pid");
    await withFakeFfmpeg(
      `echo $$ > ${pidFile}; trap '' TERM; while :; do sleep 0.05; done`,
      async () => {
        const started = Date.now();
        await assert.rejects(
          runFfmpeg(["-version"], { timeoutMs: 200, killGraceMs: 300 }),
          (error) =>
            error.code === "segmentation_timeout" && error.retryable === true,
        );
        const elapsed = Date.now() - started;
        assert.ok(
          elapsed >= 450,
          `rejected before SIGKILL escalation (${elapsed}ms)`,
        );
        const pid = Number((await readFile(pidFile, "utf8")).trim());
        assert.throws(() => process.kill(pid, 0), { code: "ESRCH" });
      },
    );
  });
});

test("ffmpeg is stopped when the render is canceled", async () => {
  await withFakeFfmpeg("sleep 30", async () => {
    let polls = 0;
    await assert.rejects(
      runFfmpeg(["-version"], {
        timeoutMs: 10_000,
        cancelPollMs: 20,
        isCanceled: async () => {
          polls += 1;
          return polls >= 3;
        },
      }),
      (error) => error.code === "render_cancelled",
    );
  });
});

test("ffmpeg failures surface the stderr tail", async () => {
  await withFakeFfmpeg(
    "echo 'Invalid data found when processing input' >&2; exit 1",
    async () => {
      await assert.rejects(
        runFfmpeg(["-version"], { timeoutMs: 5_000 }),
        /exited with 1: Invalid data found when processing input/,
      );
    },
  );
});

test("a keyed file that was never written is reported", async () => {
  await withTempDir("motionly-segmentation-nooutput-", async (root) => {
    const input = join(root, "input.mp4");
    await writeFile(input, "x");
    const upstream = await serveFiles({ "/a.mp4": input });
    const proxyLike = { origin: upstream.origin };
    try {
      await withFakeFfmpeg("exit 0", async () => {
        await assert.rejects(
          applySegmentationAlphaKey({
            sourceUrl: `${upstream.origin}/a.mp4`,
            matteUrl: `${upstream.origin}/a.mp4`,
            workDir: join(root, "work"),
            internalOrigin: proxyLike.origin,
            timeoutMs: 5_000,
          }),
          /no keyed output was written/,
        );
      });
      assert.equal(
        await stat(join(root, "work", "keyed.webm")).catch(() => null),
        null,
      );
    } finally {
      await upstream.close();
    }
  });
});

test("the keyed-video server uses the renderer's accepted URL shape and serves nothing else", async () => {
  const { startLocalFileServer } = await import("./remotion.mjs");
  await withTempDir("motionly-keyed-server-", async (root) => {
    const file = join(root, "keyed.webm");
    await writeFile(file, "webm-bytes");
    const server = await startLocalFileServer(file);
    try {
      // apps/renderer/src/manual-media-policy.ts safeRendererMediaUrl shape.
      assert.match(server.url, /^http:\/\/127\.0\.0\.1:\d+\/media\/\d+$/);
      assert.equal(await (await fetch(server.url)).text(), "webm-bytes");
      const other = await fetch(new URL("/etc/passwd", server.url));
      assert.equal(other.status, 404);
    } finally {
      await server.close();
    }
  });
});
