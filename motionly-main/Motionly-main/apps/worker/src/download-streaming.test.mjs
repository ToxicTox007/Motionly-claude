import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { downloadToFile } from "./remotion.mjs";

async function withChunkServer(chunks, callback) {
  const server = createServer((_request, response) => {
    response.writeHead(200, { "content-type": "application/octet-stream" });
    for (const chunk of chunks) response.write(chunk);
    response.end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const url = `http://127.0.0.1:${address.port}/media`;
  try {
    await callback(url);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

test("segmentation downloads stream chunks to disk", async () => {
  const root = await mkdtemp(join(tmpdir(), "motionly-download-"));
  const destination = join(root, "source.mp4");
  const chunks = [Buffer.alloc(256 * 1024, 1), Buffer.alloc(256 * 1024, 2)];
  try {
    await withChunkServer(chunks, async (url) => {
      await downloadToFile(url, destination, {
        maxBytes: 1024 * 1024,
        timeoutMs: 5_000,
        trustedInternal: true,
        internalOrigin: new URL(url).origin,
      });
    });
    const result = await readFile(destination);
    assert.equal(result.length, 512 * 1024);
    assert.equal(result[0], 1);
    assert.equal(result.at(-1), 2);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("segmentation downloads enforce byte limits and remove partial files", async () => {
  const root = await mkdtemp(join(tmpdir(), "motionly-download-limit-"));
  const destination = join(root, "source.mp4");
  try {
    await assert.rejects(
      withChunkServer([Buffer.alloc(2048, 7)], (url) =>
        downloadToFile(url, destination, {
          maxBytes: 1024,
          timeoutMs: 5_000,
          trustedInternal: true,
          internalOrigin: new URL(url).origin,
        }),
      ),
      /exceeds the 1024-byte limit/,
    );
    assert.equal(await stat(destination).catch(() => null), null);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
