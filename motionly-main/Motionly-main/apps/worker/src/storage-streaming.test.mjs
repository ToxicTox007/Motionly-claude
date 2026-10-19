import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Remotion export upload uses streaming and avoids full-file buffering", async () => {
  const remotionSource = await readFile(
    new URL("./remotion.mjs", import.meta.url),
    "utf8",
  );

  // Verify createReadStream import
  assert.match(
    remotionSource,
    /import\s+{[^}]*createReadStream[^}]*}\s+from\s+["']node:fs["']/g,
    "Should import createReadStream from node:fs",
  );

  // Extract uploadToStorage function content
  const uploadToStorageMatch = remotionSource.match(
    /async\s+function\s+uploadToStorage\([\s\S]*?\}\n/,
  );

  assert.ok(
    uploadToStorageMatch,
    "Should find uploadToStorage function in remotion.mjs",
  );
  const functionBody = uploadToStorageMatch[0];

  // Assert that createReadStream is used to initialize the upload payload
  assert.match(
    functionBody,
    /const\s+\w+\s*=\s*createReadStream\(localPath\)/,
    "Should initialize upload stream using createReadStream(localPath)",
  );

  // Assert that the stream variable is passed to the upload method
  assert.match(
    functionBody,
    /\.upload\(storagePath,\s*\w+,\s*\{/,
    "Should pass stream payload to Supabase upload",
  );

  // Assert that neither readFile nor readFileSync is called within this function scope
  assert.doesNotMatch(
    functionBody,
    /readFile/,
    "Should not use readFile within uploadToStorage",
  );
  assert.doesNotMatch(
    functionBody,
    /readFileSync/,
    "Should not use readFileSync within uploadToStorage",
  );
});
