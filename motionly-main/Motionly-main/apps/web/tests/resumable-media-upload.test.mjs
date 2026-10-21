// Task 17: one resumable, server-verified upload path for every media type.
// Source video, B-roll library clips, and style references all drive the shared
// helper in lib/resumable-upload.ts against their resumable multipart endpoints.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const root = new URL("../", import.meta.url);
const helperUrl = new URL("lib/resumable-upload.ts", root);
const helperSource = await readFile(helperUrl, "utf8");
const helperJavaScript = ts.transpileModule(helperSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: "resumable-upload.ts",
}).outputText;
const helpers = await import(
  `data:text/javascript;base64,${Buffer.from(helperJavaScript).toString("base64")}`
);

function makeFile(size, parts) {
  const bytes = new Uint8Array(size).map((_, index) => index % 251);
  return {
    name: "clip.mp4",
    size,
    type: "video/mp4",
    lastModified: 42,
    slice(start, end) {
      return {
        size: Math.min(end, size) - start,
        _bytes: bytes.slice(start, end),
      };
    },
    _parts: parts,
  };
}

test("runResumableUpload is exported as the shared orchestration entrypoint", () => {
  assert.equal(typeof helpers.runResumableUpload, "function");
});

test("runResumableUpload drives init, parts, confirm, and complete for any media type", async () => {
  const size = 25;
  const partSize = 10;
  const file = makeFile(size);
  const putCalls = [];
  const posted = [];

  const transport = {
    async initUpload() {
      return {
        session_id: "session-1",
        total_parts: 3,
        part_size_bytes: partSize,
        part_urls: [
          { part_number: 1, upload_url: "u1", upload_headers: {} },
          { part_number: 2, upload_url: "u2", upload_headers: {} },
          { part_number: 3, upload_url: "u3", upload_headers: {} },
        ],
        status: "active",
        uploaded_bytes: 0,
        completed_parts: 0,
        expires_at: "2099-01-01T00:00:00Z",
      };
    },
    async putBlob(url, blob, headers, signal, onProgress) {
      putCalls.push({ url, size: blob.size });
      onProgress(blob.size, blob.size);
      return { status: 200, etag: `etag-${url}` };
    },
    async confirmPart(sessionId, partNumber, etag) {
      posted.push({ sessionId, partNumber, etag });
      return {
        completed_parts: partNumber,
        uploaded_bytes: Math.min(partNumber * partSize, size),
      };
    },
    async completeUpload(sessionId, sha256) {
      posted.push({ complete: sessionId, sha256 });
      return {
        status: "completed",
        verified_size_bytes: size,
        verified_sha256: "a".repeat(64),
      };
    },
  };

  const progress = [];
  const result = await helpers.runResumableUpload({
    file,
    transport,
    signal: new AbortController().signal,
    onProgress: (value) => progress.push(value.percent),
  });

  assert.equal(putCalls.length, 3);
  assert.deepEqual(
    putCalls.map((call) => call.size),
    [10, 10, 5],
  );
  assert.equal(posted.filter((item) => item.partNumber).length, 3);
  assert.equal(posted.at(-1).complete, "session-1");
  assert.equal(result.status, "completed");
  assert.equal(result.verified_size_bytes, size);
  assert.equal(progress.at(-1), 100);
});

test("runResumableUpload refreshes and retries a single part on a 401/403 signed URL", async () => {
  const size = 10;
  const file = makeFile(size);
  let putAttempts = 0;

  const transport = {
    async initUpload() {
      return {
        session_id: "session-x",
        total_parts: 1,
        part_size_bytes: 10,
        part_urls: [
          { part_number: 1, upload_url: "expired", upload_headers: {} },
        ],
        status: "active",
        uploaded_bytes: 0,
        completed_parts: 0,
        expires_at: "2099-01-01T00:00:00Z",
      };
    },
    async putBlob(url) {
      putAttempts += 1;
      if (url === "expired") {
        throw new helpers.SignedUploadError("expired", 403, "");
      }
      return { status: 200, etag: "ok" };
    },
    async refreshSession() {
      return {
        status: "active",
        completed_parts: 0,
        uploaded_bytes: 0,
        pending_part_urls: [
          { part_number: 1, upload_url: "fresh", upload_headers: {} },
        ],
        expires_at: "2099-01-01T00:00:00Z",
      };
    },
    async confirmPart() {
      return { completed_parts: 1, uploaded_bytes: 10 };
    },
    async completeUpload() {
      return { status: "completed", verified_size_bytes: 10 };
    },
  };

  const result = await helpers.runResumableUpload({
    file,
    transport,
    signal: new AbortController().signal,
    onProgress: () => {},
  });
  assert.equal(putAttempts, 2);
  assert.equal(result.status, "completed");
});

test("B-roll library upload uses the shared helper and the resumable endpoints", async () => {
  const source = await readFile(
    new URL("components/editor/dnd/ElementPalette.tsx", root),
    "utf8",
  );
  assert.match(source, /runResumableUpload/);
  assert.match(source, /broll\/library\/multipart`/);
  assert.match(source, /\$\{base\}\/init/);
  assert.match(source, /\$\{[^}]+\}\/parts\/\$\{[^}]+\}\/complete/);
  assert.match(source, /\$\{[^}]+\}\/complete/);
  // The one-piece facade must be gone.
  assert.doesNotMatch(source, /broll\/library\/init(?![a-z])/);
  assert.doesNotMatch(
    source,
    /broll\/library\/\$\{initialized\.asset\.id\}\/complete/,
  );
});

test("style references upload uses the shared helper and the resumable endpoints", async () => {
  const source = await readFile(
    new URL("components/editor/ScalePanel.tsx", root),
    "utf8",
  );
  assert.match(source, /runResumableUpload/);
  assert.match(source, /references\/multipart`/);
  assert.match(source, /\$\{base\}\/init/);
  assert.match(source, /\$\{[^}]+\}\/parts\/\$\{[^}]+\}\/complete/);
  assert.match(source, /\$\{[^}]+\}\/complete/);
  assert.doesNotMatch(source, /references\/init(?![a-z])/);
  assert.doesNotMatch(source, /references\/complete(?![a-z])/);
});

test("upload panel keeps driving the source-video resumable endpoints", async () => {
  const source = await readFile(
    new URL("components/upload-panel.tsx", root),
    "utf8",
  );
  assert.match(source, /uploads\/multipart\/init/);
  assert.match(source, /uploads\/multipart\/\$\{activeSessionId\}\/complete/);
});
