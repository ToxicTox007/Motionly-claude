import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  ensureUploadRecordExists,
  persistentPythonExecutor,
  resetPythonExecutorsForTest,
  runJob,
} from "./worker.mjs";

class MockChild extends EventEmitter {
  constructor({ onWrite = null } = {}) {
    super();
    this.exitCode = null;
    this.killed = false;
    this.stdout = new EventEmitter();
    this.stderr = new EventEmitter();
    this.stdin = {
      destroyed: false,
      writable: true,
      write: (chunk, callback) => {
        const parsed = JSON.parse(String(chunk).trim());
        if (onWrite) onWrite(parsed, callback);
        else if (callback) callback(null);
        return true;
      },
      end: () => {
        this.stdin.destroyed = true;
        this.stdin.writable = false;
      },
    };
  }

  kill() {
    this.killed = true;
    this.exitCode = 1;
    this.emit("close", 1, "SIGTERM");
  }

  respond(message) {
    this.stdout.emit("data", `${JSON.stringify(message)}\n`);
  }

  respondRaw(line) {
    this.stdout.emit("data", `${line}\n`);
  }

  crash(code = 1) {
    this.exitCode = code;
    this.emit("close", code, null);
  }
}

test.afterEach(() => {
  resetPythonExecutorsForTest();
});

test("killing a Python subprocess rejects the in-flight job and the next call recovers", async () => {
  const children = [];
  const spawnImpl = () => {
    const child = new MockChild({
      onWrite: (request, callback) => {
        callback?.(null);
        if (request.control === "ping") {
          child.respond({ id: request.id, result: { ok: true } });
        } else if (children.length === 1) {
          child.crash(1);
        } else {
          child.respond({ id: request.id, result: { status: "ok" } });
        }
      },
    });
    children.push(child);
    return child;
  };

  await assert.rejects(
    persistentPythonExecutor(
      "domain",
      "app.workers.domain_job",
      { kind: "transcription" },
      {
        spawnImpl,
        heartbeatIntervalMs: 0,
        requestTimeoutMs: 25,
      },
    ),
    (error) => error.code === "subprocess_crashed",
  );

  const result = await persistentPythonExecutor(
    "domain",
    "app.workers.domain_job",
    { kind: "transcription" },
    { spawnImpl, heartbeatIntervalMs: 0, requestTimeoutMs: 25 },
  );

  assert.deepEqual(result, { status: "ok" });
  assert.equal(children.length, 2);
});

test("broken pipe is detected and surfaced", async () => {
  const spawnImpl = () => {
    let child;
    child = new MockChild({
      onWrite: (request, callback) => {
        if (request.control === "ping") {
          callback?.(null);
          child.respond({ id: request.id, result: { ok: true } });
          return;
        }
        callback?.(new Error("broken pipe"));
      },
    });
    return child;
  };

  await assert.rejects(
    persistentPythonExecutor(
      "domain",
      "app.workers.domain_job",
      { kind: "render" },
      {
        spawnImpl,
        heartbeatIntervalMs: 0,
        requestTimeoutMs: 25,
      },
    ),
    (error) => error.code === "python_broken_pipe",
  );
});

test("no-response timeout is detected", async () => {
  const childFactory = () => {
    let child;
    child = new MockChild({
      onWrite: (request, callback) => {
        callback?.(null);
        if (request.control === "ping") {
          child.respond({ id: request.id, result: { ok: true } });
        }
      },
    });
    return child;
  };

  await assert.rejects(
    persistentPythonExecutor(
      "domain",
      "app.workers.domain_job",
      { kind: "render" },
      {
        spawnImpl: childFactory,
        heartbeatIntervalMs: 0,
        requestTimeoutMs: 10,
      },
    ),
    (error) => error.code === "python_response_timeout",
  );
});

test("invalid protocol response is handled safely", async () => {
  let child;
  const spawnImpl = () => {
    child = new MockChild({
      onWrite: (request, callback) => {
        callback?.(null);
        if (request.control === "ping") {
          child.respond({ id: request.id, result: { ok: true } });
          return;
        }
        child.respondRaw("{not-json");
      },
    });
    return child;
  };

  await assert.rejects(
    persistentPythonExecutor(
      "visual",
      "app.workers.visual_job",
      { kind: "visual_analysis" },
      {
        spawnImpl,
        heartbeatIntervalMs: 0,
        requestTimeoutMs: 25,
      },
    ),
    (error) => error.code === "python_protocol_invalid",
  );
});

test("log lines before a response in the same stdout chunk do not swallow the response", async () => {
  // The visual subprocess printed debug lines on stdout; when one arrived in
  // the same chunk as the ping reply, the reply was never parsed and the job
  // waited for the full startup timeout.
  let child;
  const spawnImpl = () => {
    child = new MockChild({
      onWrite: (request, callback) => {
        callback?.(null);
        const reply = JSON.stringify(
          request.control === "ping"
            ? { id: request.id, result: { ok: true } }
            : { id: request.id, result: { ok: "done" } },
        );
        child.stdout.emit("data", `DEBUG: one\nDEBUG: two\n${reply}\n`);
      },
    });
    return child;
  };

  const result = await persistentPythonExecutor(
    "visual",
    "app.workers.visual_job",
    { kind: "visual_analysis" },
    {
      spawnImpl,
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 200,
      startupTimeoutMs: 200,
    },
  );
  assert.deepEqual(result, { ok: "done" });
});

test("subprocess restart backoff is applied and logged", async () => {
  const events = [];
  const delays = [];
  const children = [];
  const spawnImpl = () => {
    const child = new MockChild({
      onWrite: (request, callback) => {
        callback?.(null);
        if (request.control === "ping") {
          child.respond({ id: request.id, result: { ok: true } });
        } else if (children.length === 1) {
          child.crash(1);
        } else {
          child.respond({ id: request.id, result: { ok: true } });
        }
      },
    });
    children.push(child);
    return child;
  };

  await assert.rejects(
    persistentPythonExecutor(
      "matte",
      "app.workers.matte_job",
      { kind: "matte_extraction" },
      {
        spawnImpl,
        heartbeatIntervalMs: 0,
        requestTimeoutMs: 25,
        restartBackoffMs: 7,
        restartBackoffCapMs: 7,
        sleep: async (delay) => delays.push(delay),
        log: (event) => events.push(event),
      },
    ),
  );

  await persistentPythonExecutor(
    "matte",
    "app.workers.matte_job",
    { kind: "matte_extraction" },
    {
      spawnImpl,
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 25,
      restartBackoffMs: 7,
      restartBackoffCapMs: 7,
      sleep: async (delay) => delays.push(delay),
      log: (event) => events.push(event),
    },
  );

  assert.deepEqual(delays, [7]);
  assert.ok(
    events.some((event) => event.event === "subprocess_restart_scheduled"),
  );
  assert.ok(events.some((event) => event.event === "subprocess_restarted"));
});

test("restart limit is enforced and emits a structured event", async () => {
  const events = [];
  const spawnImpl = () => {
    let child;
    child = new MockChild({
      onWrite: (request, callback) => {
        callback?.(null);
        if (request.control === "ping") {
          child.respond({ id: request.id, result: { ok: true } });
        } else {
          child.crash(1);
        }
      },
    });
    return child;
  };

  await assert.rejects(
    persistentPythonExecutor(
      "domain",
      "app.workers.domain_job",
      { kind: "render" },
      {
        spawnImpl,
        heartbeatIntervalMs: 0,
        requestTimeoutMs: 25,
        maxRestartsPerWindow: 1,
        restartWindowMs: 60_000,
        log: (event) => events.push(event),
      },
    ),
  );

  await assert.rejects(
    persistentPythonExecutor(
      "domain",
      "app.workers.domain_job",
      { kind: "render" },
      {
        spawnImpl,
        heartbeatIntervalMs: 0,
        requestTimeoutMs: 25,
        maxRestartsPerWindow: 1,
        restartWindowMs: 60_000,
        log: (event) => events.push(event),
      },
    ),
    (error) => error.code === "subprocess_restart_limit_exceeded",
  );

  assert.ok(
    events.some((event) => event.event === "subprocess_restart_limit_exceeded"),
  );
});

test("upload preflight fails fast when the upload row is gone", async () => {
  process.env.SUPABASE_URL = "https://example.supabase.co";
  process.env.SUPABASE_SERVICE_ROLE_KEY = "test-service-role";
  const job = {
    data: {
      resource_id: "video-123",
      project_id: "project-123",
      workspace_id: "workspace-123",
    },
  };

  await assert.rejects(
    ensureUploadRecordExists(job, {
      fetchImpl: async (url, options) => {
        assert.match(String(url), /uploaded_videos/);
        assert.equal(options.headers.apikey, "test-service-role");
        return new Response("[]", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
    }),
    (error) => error.code === "upload_not_found",
  );
});

test("upload preflight accepts matching uploads", async () => {
  process.env.SUPABASE_URL = "https://example.supabase.co";
  process.env.SUPABASE_SERVICE_ROLE_KEY = "test-service-role";
  const job = {
    data: {
      resource_id: "video-123",
      project_id: "project-123",
      workspace_id: "workspace-123",
    },
  };

  const row = await ensureUploadRecordExists(job, {
    fetchImpl: async () =>
      new Response(JSON.stringify([{ id: "video-123", status: "ready" }]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
  });

  assert.deepEqual(row, { id: "video-123", status: "ready" });
});

test("completed business work survives telemetry and terminal progress failures", async () => {
  let executions = 0;
  const progress = [];
  const job = {
    id: "job-observability",
    queueName: "planning",
    timestamp: Date.now(),
    attemptsMade: 0,
    data: { kind: "edit_plan", trace_id: null },
    updateProgress: async (value) => {
      progress.push(value);
      if (value === 100) throw new Error("progress backend unavailable");
    },
    discard: () => assert.fail("successful work must not be discarded"),
  };

  const result = await runJob(job, {
    executeJob: async () => {
      executions += 1;
      return { status: "ready" };
    },
    reportProviderSlo: async () => {
      throw new Error("telemetry endpoint unavailable");
    },
  });

  assert.deepEqual(result, { status: "ready" });
  assert.equal(executions, 1);
  assert.deepEqual(progress, [1, 100]);
});

test("Python media supervisors use the configured application import directory", async () => {
  let spawned;
  const spawnImpl = (command, args, options) => {
    const child = new MockChild({
      onWrite: (request, callback) => {
        callback?.(null);
        child.respond({
          id: request.id,
          result: request.control === "ping" ? { ok: true } : { status: "ok" },
        });
      },
    });
    spawned = { command, args, options };
    return child;
  };

  const result = await persistentPythonExecutor(
    "domain",
    "app.workers.domain_job",
    { kind: "transcription" },
    {
      spawnImpl,
      cwd: "/app",
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 25,
    },
  );

  assert.deepEqual(result, { status: "ok" });
  assert.equal(spawned.command, "python");
  assert.deepEqual(spawned.args, ["-m", "app.workers.domain_job", "--serve"]);
  assert.equal(spawned.options.cwd, "/app");
});

test("Python worker Docker images configure the media import root", async () => {
  for (const filename of [
    "Dockerfile.python",
    "Dockerfile.gpu",
    "Dockerfile.python.demucs",
  ]) {
    const dockerfile = await readFile(
      new URL(`../${filename}`, import.meta.url),
      "utf8",
    );
    assert.match(dockerfile, /PYTHON_MEDIA_CWD=\/app/);
    assert.match(dockerfile, /WORKDIR \/app\/apps\/worker/);
  }
});
