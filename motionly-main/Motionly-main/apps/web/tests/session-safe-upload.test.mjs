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

test("multipart ranges are exact and the final part is not over-read", () => {
  assert.deepEqual(helpers.partByteRange(1, 10, 25), {
    start: 0,
    end: 10,
    size: 10,
  });
  assert.deepEqual(helpers.partByteRange(2, 10, 25), {
    start: 10,
    end: 20,
    size: 10,
  });
  assert.deepEqual(helpers.partByteRange(3, 10, 25), {
    start: 20,
    end: 25,
    size: 5,
  });
  assert.throws(() => helpers.partByteRange(4, 10, 25), RangeError);
});

test("persisted fingerprint requires the exact same browser file", () => {
  const file = {
    name: "source.mp4",
    size: 1234,
    type: "video/mp4",
    lastModified: 1_700_000_000_000,
  };
  const fingerprint = helpers.fileFingerprint(file);
  assert.deepEqual(fingerprint, file);
  assert.equal(helpers.sameFile(file, fingerprint), true);
  assert.equal(
    helpers.sameFile(
      { ...file, lastModified: file.lastModified + 1 },
      fingerprint,
    ),
    false,
  );
  assert.equal(
    helpers.sameFile({ ...file, name: "other.mp4" }, fingerprint),
    false,
  );
  assert.equal(
    helpers.sameFile({ ...file, size: file.size + 1 }, fingerprint),
    false,
  );
});

test("serialized sessions contain no token or signed upload URL", () => {
  const serialized = helpers.serializeUploadSession({
    version: 1,
    sessionId: "session-1",
    videoId: "video-1",
    expiresAt: "2099-01-01T00:00:00Z",
    totalParts: 3,
    partSizeBytes: 10,
    file: {
      name: "source.mp4",
      size: 25,
      type: "video/mp4",
      lastModified: 123,
    },
    upload_url: "https://storage.invalid/signed?token=secret",
    accessToken: "secret-token",
  });

  assert.doesNotMatch(serialized, /upload_url|storage\.invalid|token|secret/i);
  assert.deepEqual(helpers.parseUploadSession(serialized), {
    version: 1,
    sessionId: "session-1",
    videoId: "video-1",
    expiresAt: "2099-01-01T00:00:00Z",
    totalParts: 3,
    partSizeBytes: 10,
    file: {
      name: "source.mp4",
      size: 25,
      type: "video/mp4",
      lastModified: 123,
    },
  });
  assert.equal(helpers.parseUploadSession("not json"), null);
  assert.match(
    helpers.uploadSessionStorageKey("workspace/a", "project:b"),
    /^motionly:multipart-upload:v1:workspace%2Fa:project%3Ab$/,
  );
});

test("upload progress is based on confirmed server bytes plus current XHR only", () => {
  assert.deepEqual(helpers.authoritativeProgress(10, 40, 5), {
    uploadedBytes: 15,
    percent: 37,
  });
  assert.deepEqual(helpers.authoritativeProgress(40, 40, 20), {
    uploadedBytes: 40,
    percent: 100,
  });
  assert.deepEqual(helpers.authoritativeProgress(-10, 40, 0), {
    uploadedBytes: 0,
    percent: 0,
  });
});

test("upload panel uses multipart init, resume, confirmation, completion, and abort", async () => {
  const source = await readFile(
    new URL("components/upload-panel.tsx", root),
    "utf8",
  );

  assert.match(source, /uploads\/multipart\/init/);
  assert.match(source, /fetchMultipartSession\(/);
  assert.match(
    source,
    /uploads\/multipart\/\$\{activeSessionId\}\/parts\/\$\{partUrl\.part_number\}\/complete/,
  );
  assert.match(source, /uploads\/multipart\/\$\{activeSessionId\}\/complete/);
  assert.match(source, /uploads\/multipart\/\$\{sessionId\}\/abort/);
  assert.match(source, /file\.slice\(range\.start, range\.end\)/);
  assert.match(source, /pending_part_urls/);
  assert.match(source, /setProgress\([\s\S]*authoritativeProgress/);
  assert.match(
    source,
    /Cancellation could not be confirmed\. Your upload session was kept so you can retry cancellation\./,
  );
  assert.doesNotMatch(source, /Explicit cancellation still forgets/);
  assert.doesNotMatch(
    source,
    /Clearing is intentional after an explicit cancel/,
  );
  assert.doesNotMatch(source, /signed_upload_url/);
});

test("expired signed part URLs refresh server state and retry once", async () => {
  const source = await readFile(
    new URL("components/upload-panel.tsx", root),
    "utf8",
  );

  assert.match(source, /error instanceof SignedUploadError/);
  assert.match(source, /error\.status === 401 \|\| error\.status === 403/);
  assert.match(source, /refreshed\.pending_part_urls\.find/);
  assert.match(source, /uploadedPart = await uploadPart\(\)/);
  assert.match(source, /retried at most once per part/);
});

test("editor refreshes expiring source and thumbnail URLs and retries media errors", async () => {
  const [editor, preview, page] = await Promise.all([
    readFile(new URL("components/editor-client.tsx", root), "utf8"),
    readFile(
      new URL("components/editor/preview/VideoPreview.tsx", root),
      "utf8",
    ),
    readFile(new URL("app/(app)/projects/[projectId]/page.tsx", root), "utf8"),
  ]);

  assert.match(editor, /media\/refresh-urls/);
  assert.match(editor, /reference_ids: referenceIds/);
  assert.match(editor, /mediaRefreshPromiseRef/);
  assert.match(editor, /expiresAt - Date\.now\(\) - 60_000/);
  assert.match(editor, /window\.addEventListener\("focus"/);
  assert.match(editor, /document\.addEventListener\("visibilitychange"/);
  assert.match(editor, /sourceErrorRefreshUsedRef/);
  assert.match(editor, /onSourceError=\{handlePreviewSourceError\}/);
  assert.match(editor, /onPosterError=\{handlePreviewPosterError\}/);

  assert.match(preview, /onSourceError\?: \(\) => void/);
  assert.match(preview, /onError=\{onSourceError\}/);
  assert.match(preview, /playbackRestoreRef/);
  assert.match(preview, /restore\.currentTime/);
  assert.match(preview, /restore\.shouldPlay/);
  assert.match(preview, /image\.onerror/);

  assert.match(page, /source_reference_id/);
  assert.match(page, /thumbnail_reference_id/);
  assert.match(page, /expires_at/);
});
