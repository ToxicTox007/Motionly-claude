import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// `.env` is gitignored, so a fresh checkout never has one. `node --env-file=.env`
// aborts with exit code 9 when the file is missing, which made every worker
// script (lint, typecheck, build, test) fail outside a developer machine.
test("worker package scripts never hard-require a gitignored .env file", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  );
  for (const [name, command] of Object.entries(manifest.scripts)) {
    assert.doesNotMatch(
      command,
      /--env-file=/,
      `script "${name}" must not require .env; use --env-file-if-exists for local convenience`,
    );
  }
});

test("only interactive local scripts load an optional .env", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  );
  for (const [name, command] of Object.entries(manifest.scripts)) {
    if (command.includes("--env-file-if-exists")) {
      assert.ok(
        ["dev", "start"].includes(name),
        `script "${name}" should not load .env`,
      );
    }
  }
});
