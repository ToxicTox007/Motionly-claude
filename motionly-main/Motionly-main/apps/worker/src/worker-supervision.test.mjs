import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  persistentPythonExecutor,
  resetPythonExecutorsForTest,
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
    persistentPythonExecutor("domain", "app.workers.domain_job", { kind: "transcription" }, {
      spawnImpl,
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 25,
    }),
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
    persistentPythonExecutor("domain", "app.workers.domain_job", { kind: "render" }, {
      spawnImpl,
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 25,
    }),
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
    persistentPythonExecutor("domain", "app.workers.domain_job", { kind: "render" }, {
      spawnImpl: childFactory,
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 10,
    }),
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
    persistentPythonExecutor("visual", "app.workers.visual_job", { kind: "visual_analysis" }, {
      spawnImpl,
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 25,
    }),
    (error) => error.code === "python_protocol_invalid",
  );
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
    persistentPythonExecutor("matte", "app.workers.matte_job", { kind: "matte_extraction" }, {
      spawnImpl,
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 25,
      restartBackoffMs: 7,
      restartBackoffCapMs: 7,
      sleep: async (delay) => delays.push(delay),
      log: (event) => events.push(event),
    }),
  );

  await persistentPythonExecutor("matte", "app.workers.matte_job", { kind: "matte_extraction" }, {
    spawnImpl,
    heartbeatIntervalMs: 0,
    requestTimeoutMs: 25,
    restartBackoffMs: 7,
    restartBackoffCapMs: 7,
    sleep: async (delay) => delays.push(delay),
    log: (event) => events.push(event),
  });

  assert.deepEqual(delays, [7]);
  assert.ok(events.some((event) => event.event === "subprocess_restart_scheduled"));
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
    persistentPythonExecutor("domain", "app.workers.domain_job", { kind: "render" }, {
      spawnImpl,
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 25,
      maxRestartsPerWindow: 1,
      restartWindowMs: 60_000,
      log: (event) => events.push(event),
    }),
  );

  await assert.rejects(
    persistentPythonExecutor("domain", "app.workers.domain_job", { kind: "render" }, {
      spawnImpl,
      heartbeatIntervalMs: 0,
      requestTimeoutMs: 25,
      maxRestartsPerWindow: 1,
      restartWindowMs: 60_000,
      log: (event) => events.push(event),
    }),
    (error) => error.code === "subprocess_restart_limit_exceeded",
  );

  assert.ok(
    events.some((event) => event.event === "subprocess_restart_limit_exceeded"),
  );
});
