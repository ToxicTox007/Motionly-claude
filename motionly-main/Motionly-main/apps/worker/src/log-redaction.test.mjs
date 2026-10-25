import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { Writable } from "node:stream";
import test from "node:test";

import {
  createRedactingLineWriter,
  installLogRedaction,
  redactLogText,
  registerSecretValue,
  registeredSecretValueCount,
} from "./log-redaction.mjs";

const JWT =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJlc2lnbmF0dXJl";

test("bare credentials are redacted from free text", () => {
  const cases = [
    [`upstream rejected key ${JWT}`, "upstream rejected key [REDACTED_JWT]"],
    ["redis://:hunter2pass@redis:6379/0", "redis://:[REDACTED]@redis:6379/0"],
    ["postgres://app:s3cret@db:5432/x", "postgres://app:[REDACTED]@db:5432/x"],
    [`key mtly_${"a".repeat(43)}`, "key [REDACTED_API_KEY]"],
    ["AKIAABCDEFGHIJKLMNOP", "[REDACTED_AWS_KEY]"],
    ["Authorization: Bearer abc.def", "Authorization: Bearer [REDACTED]"],
    [
      "https://x/object?token=abc&expires=1",
      "https://x/object?token=[REDACTED]&expires=1",
    ],
    ['{"service_role_key": "abc"}', '{"service_role_key": "[REDACTED]"}'],
  ];
  for (const [input, expected] of cases) {
    assert.equal(redactLogText(input), expected, input);
  }
});

test("ordinary log content is untouched", () => {
  for (const text of [
    '{"event":"job_completed","queue_job_id":"render:1:2","result_status":"ok"}',
    "render 3fa85f64-5717-4562-b3fc-2c963f66afa6 at https://example.com/path",
    "ffmpeg exited with 1",
  ]) {
    assert.equal(redactLogText(text), text);
  }
});

test("a registered exact secret value is masked everywhere", () => {
  // A value with no recognisable credential shape: only exact-value
  // registration can catch it.
  const secret = "plain-operator-token-9f3a2b1c";
  assert.equal(
    redactLogText(`sent ${secret} upstream`),
    `sent ${secret} upstream`,
  );
  const before = registeredSecretValueCount();
  registerSecretValue(secret);
  assert.equal(registeredSecretValueCount(), before + 1);
  assert.equal(
    redactLogText(`sent ${secret} upstream`),
    "sent [REDACTED_SECRET] upstream",
  );
  // In JSON bodies and error text too.
  assert.ok(!redactLogText(`{"authorization":"${secret}"}`).includes(secret));
  assert.ok(
    !redactLogText(`Error: upstream rejected ${secret}`).includes(secret),
  );
});

test("short registered values are not substring-matched", () => {
  const before = registeredSecretValueCount();
  registerSecretValue("abc"); // shorter than the 8-char minimum
  assert.equal(registeredSecretValueCount(), before);
  assert.equal(redactLogText("abc def abc"), "abc def abc");
});

test("removed vendor key-prefix coverage moves to exact-value registration", () => {
  // Task 19 removed the vendor key-prefix rules. Resolved inference secrets are
  // Resolved inference secrets are registered exactly (mirroring the API), so
  // the same keys are still masked and coverage does not shrink. Keys are
  // assembled at runtime so this test source stays vendor-token-free.
  const keys = [
    "g" + "sk_" + "a".repeat(40),
    "sk-" + "or-" + "v1-" + "b".repeat(40),
    "sk-" + "c".repeat(40),
  ];
  // Unregistered, the raw text is untouched now that the prefix rules are gone.
  assert.equal(redactLogText(`key ${keys[0]}`), `key ${keys[0]}`);
  for (const key of keys) registerSecretValue(key);
  for (const key of keys) {
    const out = redactLogText(`upstream rejected ${key}`);
    assert.ok(!out.includes(key), key);
    assert.ok(out.includes("[REDACTED_SECRET]"), key);
  }
});

test("the line writer masks a registered value split across chunks", () => {
  const secret = "split-secret-value-abcdef123456";
  registerSecretValue(secret);
  const out = [];
  const writer = createRedactingLineWriter((line) => out.push(line));
  writer.write(`token ${secret.slice(0, 10)}`);
  writer.write(`${secret.slice(10)}\n`);
  writer.flush();
  assert.deepEqual(out, ["token [REDACTED_SECRET]\n"]);
});

test("ordinary content with no registered match is untouched", () => {
  for (const text of [
    '{"event":"job_completed","queue_job_id":"render:1:2","result_status":"ok"}',
    "render 3fa85f64-5717-4562-b3fc-2c963f66afa6 at https://example.com/path",
    "ffmpeg exited with 1",
  ]) {
    assert.equal(redactLogText(text), text);
  }
});

test("the line writer catches a credential split across chunks", () => {
  const out = [];
  const writer = createRedactingLineWriter((line) => out.push(line));
  writer.write(`auth failed for ${JWT.slice(0, 40)}`);
  writer.write(`${JWT.slice(40)}\nnext line`);
  writer.flush();
  assert.deepEqual(out, ["auth failed for [REDACTED_JWT]\n", "next line"]);
});

test("installed redaction covers console output and Buffer writes", () => {
  const written = [];
  const sink = () =>
    Object.assign(
      new Writable({
        write(chunk, _encoding, callback) {
          written.push(String(chunk));
          callback();
        },
      }),
      {},
    );
  const target = { stdout: sink(), stderr: sink() };
  installLogRedaction(target);
  target.stdout.write(`token ${JWT}\n`);
  target.stderr.write(Buffer.from(`redis://:pw123456@redis:6379\n`));
  assert.deepEqual(written, [
    "token [REDACTED_JWT]\n",
    "redis://:[REDACTED]@redis:6379\n",
  ]);
});

test("the worker process redacts its own logs end to end", async () => {
  const script = `
    import { installLogRedaction } from ${JSON.stringify(new URL("./log-redaction.mjs", import.meta.url).href)};
    installLogRedaction();
    console.log(JSON.stringify({ event: "x", message: "key ${JWT}" }));
    console.error("REDIS_URL=redis://:supersecret@redis:6379/0");
  `;
  const child = spawn(process.execPath, ["--input-type=module", "-e", script]);
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (data) => (stdout += data));
  child.stderr.on("data", (data) => (stderr += data));
  await new Promise((resolve) => child.on("close", resolve));
  assert.ok(!stdout.includes(JWT) && stdout.includes("[REDACTED_JWT]"), stdout);
  assert.ok(
    !stderr.includes("supersecret") && stderr.includes(":[REDACTED]@"),
    stderr,
  );
});

test("the worker entry point installs redaction before it logs anything", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(
    new URL("./worker.mjs", import.meta.url),
    "utf8",
  );
  const main = source.slice(source.indexOf("async function main() {"));
  const install = main.indexOf("installLogRedaction();");
  const firstLog = main.search(/console\.(log|error|warn|info)\(/);
  assert.ok(install > 0, "main() must call installLogRedaction()");
  assert.ok(
    firstLog === -1 || install < firstLog,
    "redaction must be installed first",
  );
  // Child output passes through the line-buffered redacting writer.
  assert.match(source, /createRedactingLineWriter\(/);
  const remotion = await readFile(
    new URL("./remotion.mjs", import.meta.url),
    "utf8",
  );
  assert.match(
    remotion,
    /proc\.stderr\.on\("data", \(data\) => stderrWriter\.write\(data\)\)/,
  );
});
