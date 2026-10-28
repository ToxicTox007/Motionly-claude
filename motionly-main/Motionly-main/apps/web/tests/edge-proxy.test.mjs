// Unit tests for the local stand-in for the edge that the browser performance
// harness runs in front of `next start` (tests/e2e/support/edge-proxy.mjs).
import assert from "node:assert/strict";
import { createServer, request } from "node:http";
import test from "node:test";

import { startEdgeProxy, upstreamFor } from "./e2e/support/edge-proxy.mjs";

const targets = {
  web: "http://127.0.0.1:3001",
  api: "http://127.0.0.1:8000",
  supabase: "http://127.0.0.1:54321",
};

test("/api and /supabase go to their services without the prefix, like the edge", () => {
  assert.deepEqual(upstreamFor("/api/projects?limit=12", targets), {
    origin: targets.api,
    path: "/projects?limit=12",
    rewritten: true,
  });
  assert.deepEqual(upstreamFor("/api", targets), {
    origin: targets.api,
    path: "/",
    rewritten: true,
  });
  assert.deepEqual(upstreamFor("/supabase/auth/v1/user", targets), {
    origin: targets.supabase,
    path: "/auth/v1/user",
    rewritten: true,
  });
});

test("everything else, including /app-api, goes to Next.js unchanged", () => {
  for (const path of [
    "/",
    "/dashboard",
    "/app-api/health",
    "/apiary",
    "/_next/static/x.js",
  ]) {
    assert.deepEqual(upstreamFor(path, targets), {
      origin: targets.web,
      path,
      rewritten: false,
    });
  }
});

function listen(handler) {
  return new Promise((resolve) => {
    const server = createServer(handler);
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

function origin(server) {
  return `http://127.0.0.1:${server.address().port}`;
}

function get(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = request(url, { headers }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () =>
        resolve({ status: res.statusCode, headers: res.headers, body }),
      );
    });
    req.on("error", reject);
    req.end();
  });
}

test("the proxy keeps the viewer's host for Next.js and forwards responses", async () => {
  const seen = [];
  const echo = (name) => (req, res) => {
    seen.push({
      name,
      url: req.url,
      host: req.headers.host,
      forwardedHost: req.headers["x-forwarded-host"],
    });
    res.setHeader("set-cookie", ["a=1; Path=/", "b=2; Path=/"]);
    res.writeHead(name === "api" ? 201 : 200, { "content-type": "text/plain" });
    res.end(name);
  };
  const web = await listen(echo("web"));
  const api = await listen(echo("api"));
  const supabase = await listen(echo("supabase"));
  const edge = await startEdgeProxy({
    port: 0,
    targets: { web: origin(web), api: origin(api), supabase: origin(supabase) },
  });
  const edgeOrigin = origin(edge);
  try {
    const page = await get(`${edgeOrigin}/login`);
    const apiCall = await get(`${edgeOrigin}/api/health/live`);
    const auth = await get(`${edgeOrigin}/supabase/auth/v1/health`);

    assert.equal(page.body, "web");
    assert.deepEqual(page.headers["set-cookie"], [
      "a=1; Path=/",
      "b=2; Path=/",
    ]);
    assert.equal(apiCall.status, 201);
    assert.equal(auth.body, "supabase");
    const [webSeen, apiSeen, supabaseSeen] = seen;
    // Server actions compare Origin with Host: Next.js must see the edge's host.
    assert.equal(webSeen.host, new URL(edgeOrigin).host);
    assert.equal(webSeen.url, "/login");
    assert.equal(apiSeen.url, "/health/live");
    assert.equal(apiSeen.host, new URL(origin(api)).host);
    assert.equal(apiSeen.forwardedHost, new URL(edgeOrigin).host);
    assert.equal(supabaseSeen.url, "/auth/v1/health");
  } finally {
    for (const server of [edge, web, api, supabase]) server.close();
  }
});

test("an unreachable upstream answers 502 instead of hanging", async () => {
  const closed = await listen(() => {});
  const deadOrigin = origin(closed);
  await new Promise((resolve) => closed.close(resolve));
  const edge = await startEdgeProxy({
    port: 0,
    targets: { web: deadOrigin, api: deadOrigin, supabase: deadOrigin },
  });
  try {
    const response = await get(`${origin(edge)}/dashboard`);
    assert.equal(response.status, 502);
  } finally {
    edge.close();
  }
});
