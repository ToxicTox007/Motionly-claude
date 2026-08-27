import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webDir = resolve(here, "..");
const validEnvelope = [
  '{"dsn":"https://public@example.glitchtip.com/42","event_id":"abc"}',
  '{"type":"event"}',
  '{"message":"bounded tunnel test"}',
].join("\n");

function runTunnelScript(source) {
  return spawnSync(process.execPath, ["--experimental-strip-types", "--input-type=module", "-e", source], {
    cwd: webDir,
    encoding: "utf8",
  });
}

function assertScriptPasses(result) {
  assert.equal(result.status, 0, `${result.stderr}\n${result.stdout}`);
}

test("GlitchTip tunnel streams a bounded body and validates envelope structure", () => {
  const source = `
    import assert from "node:assert/strict";
    import {
      TunnelHttpError,
      envelopeUrlForDsn,
      readBoundedEnvelope,
      validateEnvelope,
    } from "./app/api/glitchtip-tunnel/tunnel.ts";
    const bytes = new TextEncoder().encode(${JSON.stringify(validEnvelope)});
    const target = envelopeUrlForDsn("https://public@example.glitchtip.com/42");
    validateEnvelope(bytes, target);
    const oversizedStream = new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array(8));
        controller.enqueue(new Uint8Array(8));
        controller.close();
      },
    });
    await assert.rejects(
      readBoundedEnvelope(new Request("https://motionly.example/tunnel", {
        method: "POST", body: oversizedStream, duplex: "half",
      }), 12),
      (error) => error instanceof TunnelHttpError && error.status === 413,
    );
    assert.throws(
      () => validateEnvelope(new TextEncoder().encode("not-json\\n"), target),
      (error) => error instanceof TunnelHttpError && error.status === 400,
    );
    assert.throws(
      () => validateEnvelope(
        new TextEncoder().encode('{"dsn":"https://public@other.glitchtip.com/42"}\\n{"type":"event"}\\n{}'),
        target,
      ),
      (error) => error instanceof TunnelHttpError && error.status === 400,
    );
  `;
  assertScriptPasses(runTunnelScript(source));
});

test("GlitchTip tunnel caps concurrent forwards and bounds upstream duration", () => {
  const source = `
    import assert from "node:assert/strict";
    import {
      ForwardingGate,
      envelopeUrlForDsn,
      forwardEnvelope,
    } from "./app/api/glitchtip-tunnel/tunnel.ts";
    const envelope = new TextEncoder().encode(${JSON.stringify(validEnvelope)});
    const target = envelopeUrlForDsn("https://public@example.glitchtip.com/42");
    const gate = new ForwardingGate(1);
    const release = gate.tryAcquire();
    assert.ok(release);
    assert.equal(gate.tryAcquire(), null);
    release();
    assert.ok(gate.tryAcquire());
    const timeoutStatus = await forwardEnvelope({
      fetcher: (_url, options) => new Promise((_resolve, reject) => {
        options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
      }),
      envelope,
      envelopeUrl: target,
      contentType: "application/x-sentry-envelope",
      timeoutMs: 100,
    });
    assert.equal(timeoutStatus, 504);
    const upstreamFailure = await forwardEnvelope({
      fetcher: async () => new Response(null, { status: 500 }),
      envelope,
      envelopeUrl: target,
      contentType: "application/x-sentry-envelope",
      timeoutMs: 100,
    });
    assert.equal(upstreamFailure, 502);
  `;
  assertScriptPasses(runTunnelScript(source));
});
