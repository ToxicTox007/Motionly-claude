// Tests for the CPU-stage CloudFront simulator. Each test starts a fake
// origin and the simulator on ephemeral loopback ports; HTTPS uses a
// throwaway test CA generated with openssl.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import tls from "node:tls";
import zlib from "node:zlib";
import { after, before, beforeEach, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  cacheTtlSeconds,
  createEdge,
  loadContract,
  matchesPathPattern,
  secretFileName,
  selectBehavior,
  validateContract,
} from "./edge.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const CONTRACT_PATH = path.resolve(here, "../../infra/aws/edge-contract.json");
const SECRET = "origin-secret-for-tests";

let tlsDir;
let ca;

function makeTestCa() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cdn-sim-test-"));
  const run = (args) =>
    execFileSync("openssl", args, {
      cwd: dir,
      stdio: ["ignore", "ignore", "pipe"],
    });
  fs.writeFileSync(
    path.join(dir, "edge.ext"),
    "subjectAltName=IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n",
  );
  run([
    "req",
    "-x509",
    "-newkey",
    "ec",
    "-pkeyopt",
    "ec_paramgen_curve:prime256v1",
    "-nodes",
    "-keyout",
    "ca.key",
    "-out",
    "ca.pem",
    "-days",
    "1",
    "-subj",
    "/CN=Motionly cdn-sim test CA",
    "-addext",
    "basicConstraints=critical,CA:TRUE",
    "-addext",
    "keyUsage=critical,keyCertSign,cRLSign",
  ]);
  run([
    "req",
    "-newkey",
    "ec",
    "-pkeyopt",
    "ec_paramgen_curve:prime256v1",
    "-nodes",
    "-keyout",
    "edge.key",
    "-out",
    "edge.csr",
    "-subj",
    "/CN=127.0.0.1",
  ]);
  run([
    "x509",
    "-req",
    "-in",
    "edge.csr",
    "-CA",
    "ca.pem",
    "-CAkey",
    "ca.key",
    "-CAcreateserial",
    "-out",
    "edge.pem",
    "-days",
    "1",
    "-extfile",
    "edge.ext",
  ]);
  return dir;
}

function contractWith(changes = {}) {
  const contract = structuredClone(loadContract(CONTRACT_PATH));
  Object.assign(contract.origin, changes.origin ?? {});
  return contract;
}

function startOrigin(handler) {
  const requests = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const record = {
        method: req.method,
        url: req.url,
        headers: req.headers,
        body: Buffer.concat(chunks),
      };
      requests.push(record);
      handler(record, res, req);
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () =>
      resolve({
        server,
        requests,
        url: `http://127.0.0.1:${server.address().port}`,
      }),
    );
  });
}

async function startEdge(
  origin,
  { contract = contractWith(), ...options } = {},
) {
  const edge = createEdge({
    contract,
    originUrl: origin.url,
    tls: {
      cert: fs.readFileSync(path.join(tlsDir, "edge.pem")),
      key: fs.readFileSync(path.join(tlsDir, "edge.key")),
    },
    customHeaderValues: { "X-Motionly-Origin-Verify": SECRET },
    log: () => {},
    ...options,
  });
  await edge.listen({ httpsPort: 0, httpPort: 0, host: "127.0.0.1" });
  return edge;
}

function fetchEdge(
  edge,
  pathname,
  { method = "GET", headers = {}, body, plain = false } = {},
) {
  const port = plain ? edge.ports.http : edge.ports.https;
  const transport = plain ? http : https;
  return new Promise((resolve, reject) => {
    const req = transport.request(
      {
        host: "127.0.0.1",
        port,
        method,
        path: pathname,
        headers: { host: `127.0.0.1:${port}`, ...headers },
        agent: false,
        ...(plain ? {} : { ca }),
      },
      (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () =>
          resolve({
            status: res.statusCode,
            headers: res.headers,
            body: Buffer.concat(chunks),
          }),
        );
      },
    );
    req.on("error", reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

function closeOrigin(origin) {
  return new Promise((resolve) => {
    origin.server.close(resolve);
    origin.server.closeAllConnections();
  });
}

const closers = [];
async function closeAll() {
  while (closers.length) await closers.pop()();
}

before(() => {
  tlsDir = makeTestCa();
  ca = fs.readFileSync(path.join(tlsDir, "ca.pem"));
});
after(() => fs.rmSync(tlsDir, { recursive: true, force: true }));
beforeEach(closeAll);
after(closeAll);

async function setup(handler, options) {
  const origin = await startOrigin(handler);
  closers.push(() => closeOrigin(origin));
  const edge = await startEdge(origin, options);
  closers.push(() => edge.close());
  return { origin, edge };
}

const ok =
  (body = "ok", headers = {}) =>
  (record, res) => {
    res.writeHead(200, {
      "content-type": "text/plain",
      "content-length": Buffer.byteLength(body),
      ...headers,
    });
    res.end(body);
  };

describe("contract", () => {
  test("path patterns follow CloudFront semantics", () => {
    for (const [pattern, value, expected] of [
      ["/api/*", "/api/projects", true],
      ["/api/*", "/api/", true],
      ["/api/*", "/api", false],
      ["/api/*", "/API/projects", false],
      ["/icon.svg", "/icon.svg", true],
      ["/icon.svg", "/icon.svgz", false],
      ["/img/?.png", "/img/a.png", true],
      ["/img/?.png", "/img/ab.png", false],
      ["*", "/anything", true],
    ]) {
      assert.equal(
        matchesPathPattern(pattern, value),
        expected,
        `${pattern} ${value}`,
      );
    }
  });

  test("requests are routed to the first matching behavior", () => {
    const contract = loadContract(CONTRACT_PATH);
    assert.equal(
      selectBehavior(contract, "/_next/static/chunks/a.js").path_pattern,
      "/_next/static/*",
    );
    assert.equal(
      selectBehavior(contract, "/api/projects").path_pattern,
      "/api/*",
    );
    assert.equal(
      selectBehavior(contract, "/supabase/auth/v1/token").path_pattern,
      "/supabase/*",
    );
    assert.equal(
      selectBehavior(contract, "/pay/checkout/order_1").path_pattern,
      "/pay/*",
    );
    assert.equal(selectBehavior(contract, "/dashboard").path_pattern, "*");
  });

  test("a contract the simulator cannot honour is rejected", () => {
    const contract = structuredClone(loadContract(CONTRACT_PATH));
    contract.behaviors[0].cache_policy = "Managed-Unknown";
    assert.throws(() => validateContract(contract), /unknown cache policy/);
    const https = structuredClone(loadContract(CONTRACT_PATH));
    https.origin.protocol_policy = "https-only";
    assert.throws(() => validateContract(https), /protocol_policy/);
  });

  test("custom header secrets map to secret file names", () => {
    assert.equal(
      secretFileName("ssm:/motionly/{environment}/edge/origin-verify"),
      "edge_origin_verify",
    );
    assert.throws(() => secretFileName("literal-value"), /value_source/);
  });

  test("TTLs follow CloudFront's rules for each cache policy", () => {
    const contract = loadContract(CONTRACT_PATH);
    const optimized = contract.cache_policies["Managed-CachingOptimized"];
    const disabled = contract.cache_policies["Managed-CachingDisabled"];
    assert.equal(cacheTtlSeconds(optimized, {}), 86400);
    assert.equal(
      cacheTtlSeconds(optimized, { "cache-control": "public, max-age=60" }),
      60,
    );
    assert.equal(
      cacheTtlSeconds(optimized, { "cache-control": "max-age=0" }),
      1,
    );
    assert.equal(
      cacheTtlSeconds(optimized, { "cache-control": "max-age=99999999" }),
      31536000,
    );
    assert.equal(
      cacheTtlSeconds(optimized, {
        "cache-control": "max-age=60, s-maxage=600",
      }),
      600,
    );
    // A minimum TTL above zero caches even no-store responses.
    assert.equal(
      cacheTtlSeconds(optimized, { "cache-control": "no-store" }),
      1,
    );
    const now = Date.parse("2026-10-07T00:00:00Z");
    assert.equal(
      cacheTtlSeconds(
        optimized,
        { expires: "Wed, 07 Oct 2026 00:02:00 GMT" },
        now,
      ),
      120,
    );
    assert.equal(
      cacheTtlSeconds(disabled, { "cache-control": "max-age=600" }),
      0,
    );
  });
});

describe("viewer protocol", () => {
  test("plain HTTP is redirected to HTTPS: 301 for GET, 307 for other methods", async () => {
    const { origin, edge } = await setup(ok());

    const get = await fetchEdge(edge, "/dashboard?tab=1", { plain: true });
    assert.equal(get.status, 301);
    assert.equal(
      get.headers.location,
      `https://127.0.0.1:${edge.ports.https}/dashboard?tab=1`,
    );
    const post = await fetchEdge(edge, "/api/projects", {
      plain: true,
      method: "POST",
      body: "{}",
    });
    assert.equal(post.status, 307);
    assert.equal(origin.requests.length, 0);
  });

  test("redirects use the public HTTPS port when the edge sits behind a port mapping", async () => {
    const { edge } = await setup(ok(), { publicHttpsPort: 18443 });
    const get = await fetchEdge(edge, "/login", {
      plain: true,
      headers: { host: "127.0.0.1:18080" },
    });
    assert.equal(get.headers.location, "https://127.0.0.1:18443/login");
  });
});

describe("origin requests", () => {
  test("dynamic paths reach the origin with the whole viewer request plus edge headers", async () => {
    const { origin, edge } = await setup(ok());

    const response = await fetchEdge(edge, "/api/projects?limit=5", {
      headers: {
        authorization: "Bearer user-token",
        cookie: "sb-motionly-auth-token=abc",
        "x-motionly-origin-verify": "forged-by-viewer",
        "x-forwarded-proto": "http",
        "x-forwarded-for": "203.0.113.9",
        "cloudfront-viewer-address": "198.51.100.1:1",
        "x-custom": "kept",
      },
    });

    assert.equal(response.status, 200);
    const [seen] = origin.requests;
    assert.equal(seen.url, "/api/projects?limit=5");
    assert.equal(seen.headers.host, `127.0.0.1:${edge.ports.https}`);
    assert.equal(seen.headers.authorization, "Bearer user-token");
    assert.equal(seen.headers.cookie, "sb-motionly-auth-token=abc");
    assert.equal(seen.headers["x-custom"], "kept");
    // The origin secret always comes from the edge, never the viewer.
    assert.equal(seen.headers["x-motionly-origin-verify"], SECRET);
    assert.equal(seen.headers["x-forwarded-proto"], undefined);
    assert.equal(seen.headers["cloudfront-forwarded-proto"], "https");
    assert.match(
      seen.headers["cloudfront-viewer-address"],
      /^127\.0\.0\.1:\d+$/,
    );
    assert.equal(seen.headers["cloudfront-viewer-http-version"], "1.1");
    assert.match(seen.headers["cloudfront-viewer-tls"], /^TLSv1\.[23]:/);
    assert.equal(seen.headers["x-forwarded-for"], "203.0.113.9, 127.0.0.1");
    assert.match(
      seen.headers.via,
      /^1\.1 [0-9a-f]+\.cloudfront\.net \(CloudFront\)$/,
    );
    assert.match(seen.headers["x-amz-cf-id"], /^[A-Za-z0-9_-]{20,}$/);
    assert.equal(response.headers["x-amz-cf-id"], seen.headers["x-amz-cf-id"]);
  });

  test("cached static paths forward only the Host header", async () => {
    const { origin, edge } = await setup(ok("x".repeat(10)));

    await fetchEdge(edge, "/_next/static/chunks/app.js?v=1", {
      headers: {
        authorization: "Bearer user-token",
        cookie: "a=b",
        "x-custom": "dropped",
      },
    });

    const [seen] = origin.requests;
    assert.equal(seen.url, "/_next/static/chunks/app.js");
    assert.equal(seen.headers.host, `127.0.0.1:${edge.ports.https}`);
    for (const name of ["authorization", "cookie", "x-custom"]) {
      assert.equal(seen.headers[name], undefined, name);
    }
    assert.equal(seen.headers["x-motionly-origin-verify"], SECRET);
    assert.equal(seen.headers["user-agent"], "Amazon CloudFront");
  });

  test("a caching behavior never forwards Authorization, whatever its origin request policy", async () => {
    const contract = contractWith();
    contract.behaviors[0].origin_request_policy =
      "Managed-AllViewerAndCloudFrontHeaders-2022-06";
    const { origin, edge } = await setup(ok(), { contract });

    await fetchEdge(edge, "/_next/static/chunks/app.js", {
      headers: { authorization: "Bearer user-token", cookie: "a=b" },
    });

    assert.equal(origin.requests[0].headers.authorization, undefined);
    assert.equal(origin.requests[0].headers.cookie, "a=b");
  });

  test("request bodies reach the origin intact", async () => {
    const { origin, edge } = await setup(ok());
    const body = crypto.randomBytes(2 * 1024 * 1024);

    const response = await fetchEdge(
      edge,
      "/supabase/storage/v1/object/upload/sign/raw/x",
      {
        method: "PUT",
        headers: {
          "content-type": "application/octet-stream",
          "content-length": body.length,
        },
        body,
      },
    );

    assert.equal(response.status, 200);
    assert.equal(origin.requests[0].method, "PUT");
    assert.ok(origin.requests[0].body.equals(body));
  });

  test("methods a behavior does not allow are refused at the edge", async () => {
    const { origin, edge } = await setup(ok());

    const response = await fetchEdge(edge, "/_next/static/chunks/app.js", {
      method: "POST",
      body: "x",
    });

    assert.equal(response.status, 403);
    assert.equal(response.headers["x-cache"], "Error from cloudfront");
    assert.equal(origin.requests.length, 0);
  });

  test("a GET request with a body is refused", async () => {
    const { origin, edge } = await setup(ok());
    const response = await fetchEdge(edge, "/api/projects", {
      headers: { "content-length": "4" },
      body: "body",
    });
    assert.equal(response.status, 403);
    assert.equal(origin.requests.length, 0);
  });
});

describe("caching", () => {
  test("static assets are cached, compressed per encoding, and served with Age", async () => {
    const asset = "console.log('motionly');\n".repeat(100);
    const { origin, edge } = await setup(
      ok(asset, {
        "content-type": "application/javascript",
        "cache-control": "public, max-age=31536000, immutable",
      }),
    );

    const first = await fetchEdge(edge, "/_next/static/chunks/app.js", {
      headers: { "accept-encoding": "gzip, br" },
    });
    // Normalized Accept-Encoding is the cache key: order and other codings are ignored.
    const second = await fetchEdge(edge, "/_next/static/chunks/app.js", {
      headers: { "accept-encoding": "deflate, br, gzip" },
    });
    const gzip = await fetchEdge(edge, "/_next/static/chunks/app.js", {
      headers: { "accept-encoding": "gzip" },
    });
    const identity = await fetchEdge(edge, "/_next/static/chunks/app.js");

    assert.equal(first.headers["x-cache"], "Miss from cloudfront");
    assert.equal(first.headers["content-encoding"], "br");
    assert.equal(zlib.brotliDecompressSync(first.body).toString(), asset);
    assert.equal(second.headers["x-cache"], "Hit from cloudfront");
    assert.match(second.headers.age, /^\d+$/);
    assert.equal(gzip.headers["content-encoding"], "gzip");
    assert.equal(zlib.gunzipSync(gzip.body).toString(), asset);
    assert.equal(identity.headers["content-encoding"], undefined);
    assert.equal(identity.body.toString(), asset);
    // One origin request per normalized encoding (br, gzip, none).
    assert.equal(origin.requests.length, 3);
    assert.equal(origin.requests[0].headers["accept-encoding"], "br,gzip");
  });

  test("small, unsized or already-encoded objects are not compressed", async () => {
    const { edge } = await setup((record, res) => {
      if (record.url.endsWith("small.js")) {
        res.writeHead(200, {
          "content-type": "application/javascript",
          "content-length": 999,
        });
        return res.end("x".repeat(999));
      }
      if (record.url.endsWith("unsized.js")) {
        // Chunked: CloudFront needs Content-Length to decide whether to compress.
        res.writeHead(200, { "content-type": "application/javascript" });
        return res.end("z".repeat(5000));
      }
      const encoded = zlib.gzipSync("y".repeat(5000));
      res.writeHead(200, {
        "content-type": "application/javascript",
        "content-encoding": "gzip",
        "content-length": encoded.length,
      });
      res.end(encoded);
    });

    for (const name of ["small", "unsized"]) {
      const response = await fetchEdge(edge, `/_next/static/${name}.js`, {
        headers: { "accept-encoding": "br" },
      });
      assert.equal(response.headers["content-encoding"], undefined, name);
    }
    const encoded = await fetchEdge(edge, "/_next/static/encoded.js", {
      headers: { "accept-encoding": "br" },
    });
    assert.equal(encoded.headers["content-encoding"], "gzip");
    assert.equal(zlib.gunzipSync(encoded.body).toString(), "y".repeat(5000));
  });

  test("a no-store asset is still cached for the minimum TTL", async () => {
    const { origin, edge } = await setup(
      ok("a", { "cache-control": "no-store" }),
    );

    await fetchEdge(edge, "/icon.svg");
    const cached = await fetchEdge(edge, "/icon.svg");
    await new Promise((resolve) => setTimeout(resolve, 1100));
    const expired = await fetchEdge(edge, "/icon.svg");

    assert.equal(cached.headers["x-cache"], "Hit from cloudfront");
    assert.equal(expired.headers["x-cache"], "Miss from cloudfront");
    assert.equal(origin.requests.length, 2);
  });

  test("dynamic responses and errors are never cached", async () => {
    const { origin, edge } = await setup((record, res) => {
      if (record.url.startsWith("/_next/static/missing")) {
        res.writeHead(404, {
          "content-type": "text/plain",
          "cache-control": "max-age=600",
        });
        return res.end("missing");
      }
      res.writeHead(200, {
        "content-type": "text/html",
        "cache-control": "public, max-age=600",
      });
      res.end("<html></html>");
    });

    for (let index = 0; index < 2; index += 1) {
      await fetchEdge(edge, "/dashboard");
      await fetchEdge(edge, "/_next/static/missing.js");
    }

    assert.equal(origin.requests.length, 4);
  });
});

describe("responses", () => {
  test("the response headers policy adds edge security headers without replacing the app's", async () => {
    const { edge } = await setup(
      ok("<html></html>", {
        "content-type": "text/html",
        "x-frame-options": "SAMEORIGIN",
        "content-security-policy": "script-src 'nonce-abc'",
        server: "uvicorn",
        "x-kong-upstream-latency": "3",
        "strict-transport-security": "max-age=1",
      }),
    );

    const response = await fetchEdge(edge, "/login");

    assert.equal(
      response.headers["strict-transport-security"],
      "max-age=63072000",
    );
    assert.equal(response.headers["x-frame-options"], "SAMEORIGIN");
    assert.equal(response.headers["x-content-type-options"], "nosniff");
    assert.equal(
      response.headers["referrer-policy"],
      "strict-origin-when-cross-origin",
    );
    assert.equal(
      response.headers["content-security-policy"],
      "script-src 'nonce-abc'",
    );
    assert.equal(response.headers.server, undefined);
    assert.equal(response.headers["x-kong-upstream-latency"], undefined);
    assert.match(response.headers.via, /cloudfront\.net \(CloudFront\)$/);
  });

  test("an unreachable origin yields 502 after every connection attempt", async () => {
    const origin = await startOrigin(ok());
    const url = origin.url;
    await closeOrigin(origin);
    const edge = await startEdge(
      { url },
      { contract: contractWith({ origin: { connection_timeout_seconds: 1 } }) },
    );
    closers.push(() => edge.close());

    const response = await fetchEdge(edge, "/dashboard");

    assert.equal(response.status, 502);
    assert.equal(response.headers["x-cache"], "Error from cloudfront");
  });

  test("a slow origin yields 504; GET is retried and POST is not", async () => {
    const { origin, edge } = await setup(
      () => {
        /* never answers */
      },
      {
        contract: contractWith({
          origin: { response_timeout_seconds: 1, connection_attempts: 2 },
        }),
      },
    );

    const get = await fetchEdge(edge, "/dashboard");
    const getAttempts = origin.requests.length;
    const post = await fetchEdge(edge, "/api/projects", {
      method: "POST",
      body: "{}",
    });

    assert.equal(get.status, 504);
    assert.equal(post.status, 504);
    assert.equal(getAttempts, 2);
    assert.equal(origin.requests.length, 3);
  });

  test("the keep-alive timeout applies only to idle pooled connections, never mid-response", async () => {
    const ports = [];
    const { edge } = await setup(
      (record, res, req) => {
        ports.push(req.socket.remotePort);
        res.writeHead(200, {
          "content-type": "text/plain",
          "cache-control": "no-store",
        });
        if (!record.url.endsWith("/slow")) return res.end("fast");
        // A streamed response that pauses longer than the keep-alive timeout.
        res.write("first ");
        setTimeout(() => res.end("second"), 1_500);
      },
      { contract: contractWith({ origin: { keepalive_timeout_seconds: 1 } }) },
    );

    const warnings = [];
    const onWarning = (warning) => warnings.push(warning.name);
    process.on("warning", onWarning);
    for (let i = 0; i < 12; i += 1) {
      assert.equal(
        (await fetchEdge(edge, "/api/fast")).body.toString(),
        "fast",
      );
    }
    const slow = await fetchEdge(edge, "/api/slow");

    assert.equal(slow.status, 200);
    assert.equal(slow.body.toString(), "first second");
    // Every request reused the one pooled origin connection.
    assert.equal(new Set(ports).size, 1);
    await new Promise((resolve) => setImmediate(resolve));
    process.off("warning", onWarning);
    // The idle timer is set once per pooled socket, not once per reuse.
    assert.deepEqual(warnings, []);
  });
});

describe("websockets", () => {
  test("Realtime WebSockets are tunnelled to the origin with the origin secret", async () => {
    const origin = await startOrigin(ok());
    closers.push(() => closeOrigin(origin));
    // See openRealtimeTunnel: upgraded sockets must be destroyed explicitly.
    const upgradedSockets = new Set();
    closers.push(() => {
      for (const socket of upgradedSockets) socket.destroy();
    });
    let upgradeHeaders;
    origin.server.on("upgrade", (req, socket) => {
      upgradedSockets.add(socket);
      upgradeHeaders = req.headers;
      const accept = crypto
        .createHash("sha1")
        .update(
          `${req.headers["sec-websocket-key"]}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`,
        )
        .digest("base64");
      socket.write(
        `HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`,
      );
      socket.on("data", (data) => socket.write(data));
    });
    const edge = await startEdge(origin);
    closers.push(() => edge.close());

    const reply = await new Promise((resolve, reject) => {
      const socket = tls.connect(
        {
          host: "127.0.0.1",
          port: edge.ports.https,
          ca,
          servername: "localhost",
        },
        () => {
          socket.write(
            "GET /supabase/realtime/v1/websocket?apikey=x HTTP/1.1\r\n" +
              `Host: 127.0.0.1:${edge.ports.https}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n` +
              "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n",
          );
        },
      );
      let buffer = "";
      socket.on("data", (data) => {
        buffer += data.toString();
        if (buffer.includes("\r\n\r\n") && !buffer.includes("echo-me"))
          socket.write("echo-me");
        if (buffer.includes("echo-me")) {
          socket.destroy();
          resolve(buffer);
        }
      });
      socket.on("error", reject);
    });

    assert.match(reply, /^HTTP\/1\.1 101 /);
    assert.match(reply, /echo-me$/);
    assert.equal(upgradeHeaders["x-motionly-origin-verify"], SECRET);
    assert.equal(upgradeHeaders["cloudfront-forwarded-proto"], "https");
    assert.equal(upgradeHeaders.upgrade, "websocket");
  });

  // A Realtime client sends a heartbeat on its channel well inside the idle
  // timeout (Supabase heartbeats every ~30s, the browser spec uses the same
  // channel). The edge resets the idle timer on any frame, so the tunnel must
  // survive many idle windows as long as heartbeats keep arriving — and must be
  // cut once they stop. Drive a short window instead of waiting ten minutes.
  async function openRealtimeTunnel(idleMs) {
    const origin = await startOrigin(ok());
    closers.push(() => closeOrigin(origin));
    // Upgraded sockets leave the HTTP server's bookkeeping, so
    // closeAllConnections() does not reach them and server.close() would wait
    // forever: destroy them first (closers run last-in, first-out).
    const upgradedSockets = new Set();
    closers.push(() => {
      for (const socket of upgradedSockets) socket.destroy();
    });
    origin.server.on("upgrade", (req, socket) => {
      upgradedSockets.add(socket);
      const accept = crypto
        .createHash("sha1")
        .update(
          `${req.headers["sec-websocket-key"]}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`,
        )
        .digest("base64");
      socket.write(
        `HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`,
      );
      // Mirror every heartbeat frame back to the viewer.
      socket.on("data", (data) => socket.write(data));
    });
    const edge = await startEdge(origin, { websocketIdleMs: idleMs });
    closers.push(() => edge.close());

    const socket = tls.connect({
      host: "127.0.0.1",
      port: edge.ports.https,
      ca,
      servername: "localhost",
    });
    await new Promise((resolve, reject) => {
      socket.once("secureConnect", resolve);
      socket.once("error", reject);
    });
    const upgraded = new Promise((resolve) => {
      let buffer = "";
      socket.on("data", (data) => {
        buffer += data.toString();
        if (buffer.includes("\r\n\r\n")) resolve();
      });
    });
    socket.write(
      "GET /supabase/realtime/v1/websocket?apikey=x HTTP/1.1\r\n" +
        `Host: 127.0.0.1:${edge.ports.https}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n` +
        "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n",
    );
    await upgraded;
    return socket;
  }

  test("a heartbeating Realtime socket survives the idle cutoff", async () => {
    const idleMs = 150;
    const socket = await openRealtimeTunnel(idleMs);

    let echoes = 0;
    socket.on("data", (data) => {
      if (data.toString().includes("heartbeat")) echoes += 1;
    });
    // Beat every ~idle/3, past several whole idle windows.
    const beats = 6;
    for (let i = 0; i < beats; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, idleMs / 3));
      assert.equal(
        socket.destroyed,
        false,
        `tunnel closed before heartbeat ${i + 1}`,
      );
      socket.write("heartbeat");
    }
    // Total elapsed (~2.5 idle windows) far exceeds a single idle timeout.
    await new Promise((resolve) => setTimeout(resolve, idleMs / 3));
    assert.equal(socket.destroyed, false, "heartbeating tunnel was cut");
    assert.ok(echoes >= 1, "origin never saw a heartbeat through the tunnel");
    socket.destroy();
  });

  test("a silent Realtime socket is cut at the idle cutoff", async () => {
    const idleMs = 150;
    const socket = await openRealtimeTunnel(idleMs);

    const closed = new Promise((resolve) => {
      socket.on("close", () => resolve(true));
      socket.on("end", () => resolve(true));
    });
    // Send nothing: the tunnel must hit the idle timeout and close.
    const result = await Promise.race([
      closed,
      new Promise((resolve) => setTimeout(() => resolve(false), idleMs * 8)),
    ]);
    assert.equal(result, true, "silent tunnel was not cut at the idle cutoff");
  });
});
