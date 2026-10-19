// Task 10: sessions survive access-token expiry, signed-in pages are guarded
// and renewed, and the login page only ever returns to this site.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (path) => readFileSync(resolve(webDir, path), "utf8");
const ORIGIN = "https://d111abcdef8.cloudfront.net";

// --- redirects (B14) -------------------------------------------------------

test("the login page returns only to paths on this site", async () => {
  const { safeNextPath } = await import("../lib/safe-redirect.mjs");
  const offSite = [
    "/\\evil.example",
    "/\\/evil.example",
    "\\\\evil.example",
    "//evil.example",
    "///evil.example",
    "/a/..//evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "https://evil.example/dashboard",
    "http:evil.example",
    "javascript:alert(1)",
    "dashboard",
    `${ORIGIN}.evil.example/dashboard`,
  ];
  for (const value of offSite) {
    assert.equal(
      safeNextPath(value, ORIGIN),
      "/dashboard",
      JSON.stringify(value),
    );
  }
  assert.equal(safeNextPath(null, ORIGIN), "/dashboard");
  assert.equal(safeNextPath("", ORIGIN), "/dashboard");
  assert.equal(
    safeNextPath("/projects/abc?tab=audio#top", ORIGIN),
    "/projects/abc?tab=audio#top",
  );
  assert.equal(safeNextPath("/admin/ops", ORIGIN), "/admin/ops");
  // An absolute URL on this origin is reduced to its path.
  assert.equal(safeNextPath(`${ORIGIN}/billing`, ORIGIN), "/billing");
  assert.equal(safeNextPath("//evil.example", ORIGIN, "/login"), "/login");
});

test("sign-in redirects go through the shared validator", () => {
  const actions = read("app/(auth)/actions.ts");
  assert.match(actions, /from "@\/lib\/safe-redirect\.mjs"/);
  assert.doesNotMatch(actions, /function safeRedirectPath/);
  assert.match(actions, /safeNextPath\(formData\.get\("next"\), env\.appUrl\)/);
});

// --- signed-in pages -------------------------------------------------------

test("every signed-in page is guarded, including /admin", async () => {
  const { SIGNED_IN_PREFIXES, isSignedInPath } =
    await import("../lib/signed-in-routes.mjs");
  const group = join(webDir, "app", "(app)");
  const sections = readdirSync(group).filter((name) =>
    statSync(join(group, name)).isDirectory(),
  );
  assert.ok(sections.includes("admin"));
  for (const section of sections) {
    assert.ok(
      SIGNED_IN_PREFIXES.includes(`/${section}`),
      `/${section} is not guarded`,
    );
    assert.ok(isSignedInPath(`/${section}`));
    assert.ok(isSignedInPath(`/${section}/anything/deeper`));
  }
  for (const path of [
    "/",
    "/login",
    "/signup",
    "/pricing",
    "/review/abc",
    "/terms",
    "/administrator",
    "/projectsx",
  ]) {
    assert.equal(isSignedInPath(path), false, path);
  }
});

test("the proxy renews the session on signed-in pages and sends the viewer back after login", () => {
  const proxy = read("proxy.ts");
  assert.match(proxy, /from "@\/lib\/signed-in-routes\.mjs"/);
  assert.match(proxy, /isSignedInPath\(request\.nextUrl\.pathname\)/);
  assert.doesNotMatch(proxy, /protectedPrefixes/);
  assert.match(proxy, /renewSession\(supabase\.auth\)/);
});

test("the proxy renews a token before the server components would", async () => {
  const { SESSION_RENEWAL_MARGIN_MS, renewSession } =
    await import("../lib/supabase/session-renewal.mjs");
  // auth-js renews tokens within 90 s of expiry. A server component cannot
  // save the rotated refresh token, so the proxy renews earlier: in the same
  // request, a server component then always sees a fresh token.
  assert.ok(SESSION_RENEWAL_MARGIN_MS >= 90_000 + 30_000);

  const now = 1_800_000_000_000;
  const fakeAuth = (expiresInMs, user = { id: "user-1" }) => {
    const calls = [];
    return {
      calls,
      async getSession() {
        calls.push("getSession");
        return {
          data: {
            session:
              expiresInMs === null
                ? null
                : { expires_at: Math.floor((now + expiresInMs) / 1000) },
          },
        };
      },
      async refreshSession() {
        calls.push("refreshSession");
        return {
          data: { session: { expires_at: now / 1000 + 3600 } },
          error: null,
        };
      },
      async getUser() {
        calls.push("getUser");
        return { data: { user } };
      },
    };
  };

  const expiring = fakeAuth(SESSION_RENEWAL_MARGIN_MS - 1000);
  assert.deepEqual(await renewSession(expiring, now), {
    user: { id: "user-1" },
  });
  assert.deepEqual(expiring.calls, ["getSession", "refreshSession", "getUser"]);

  const fresh = fakeAuth(SESSION_RENEWAL_MARGIN_MS + 1000);
  await renewSession(fresh, now);
  assert.deepEqual(fresh.calls, ["getSession", "getUser"]);

  // A visitor without a session costs no Auth call beyond reading cookies.
  const anonymous = fakeAuth(null);
  assert.deepEqual(await renewSession(anonymous, now), { user: null });
  assert.deepEqual(anonymous.calls, ["getSession"]);
});

// --- the browser's API token (N4) -----------------------------------------

function fakeBrowserAuth(tokens) {
  const calls = [];
  let current = 0;
  return {
    calls,
    async getSession() {
      calls.push("getSession");
      const token = tokens[current];
      return { data: { session: token ? { access_token: token } : null } };
    },
    async refreshSession() {
      calls.push("refreshSession");
      current += 1;
      const token = tokens[current];
      return {
        data: { session: token ? { access_token: token } : null },
        error: token ? null : new Error("refresh failed"),
      };
    },
  };
}

function fakeFetch(statuses) {
  const requests = [];
  const fetch = async (url, init) => {
    requests.push({ url, init });
    return new Response("{}", { status: statuses[requests.length - 1] ?? 200 });
  };
  return { fetch, requests };
}

test("API calls use the browser's current session token, never a page-load copy", async () => {
  const { fetchWithSession, SessionExpiredError } =
    await import("../lib/api-session.mjs");
  const auth = fakeBrowserAuth(["token-a"]);
  const calls = fakeFetch([200, 200]);
  await fetchWithSession(
    auth,
    "/api/x",
    { method: "POST" },
    {
      workspaceId: "ws-1",
      json: true,
      fetch: calls.fetch,
    },
  );
  await fetchWithSession(
    auth,
    "/api/x",
    {},
    { workspaceId: "ws-1", fetch: calls.fetch },
  );
  assert.deepEqual(calls.requests[0].init.headers, {
    "content-type": "application/json",
    "x-motionly-workspace-id": "ws-1",
    authorization: "Bearer token-a",
  });
  assert.deepEqual(calls.requests[1].init.headers, {
    "x-motionly-workspace-id": "ws-1",
    authorization: "Bearer token-a",
  });
  // The session is read for every call: nothing is cached.
  assert.deepEqual(auth.calls, ["getSession", "getSession"]);
  await assert.rejects(
    fetchWithSession(
      fakeBrowserAuth([]),
      "/api/x",
      {},
      {
        workspaceId: "ws-1",
        fetch: calls.fetch,
      },
    ),
    (error) =>
      error instanceof SessionExpiredError &&
      /session expired/i.test(error.message),
  );
  assert.equal(
    calls.requests.length,
    2,
    "no request is sent without a session",
  );
});

test("a rejected token is renewed and the call retried exactly once", async () => {
  const { fetchWithSession } = await import("../lib/api-session.mjs");

  const renewed = fakeBrowserAuth(["old", "new"]);
  const first = fakeFetch([401, 200]);
  const response = await fetchWithSession(
    renewed,
    "/api/projects/p/plans",
    { method: "POST", body: '{"a":1}', headers: { "x-extra": "1" } },
    { workspaceId: "ws-1", json: true, fetch: first.fetch },
  );
  assert.equal(response.status, 200);
  assert.deepEqual(renewed.calls, ["getSession", "refreshSession"]);
  assert.deepEqual(
    first.requests.map((request) => request.init.headers.authorization),
    ["Bearer old", "Bearer new"],
  );
  for (const request of first.requests) {
    assert.equal(request.init.method, "POST");
    assert.equal(request.init.body, '{"a":1}');
    assert.equal(request.init.headers["x-extra"], "1");
  }

  // Still rejected after a renewal: the 401 is returned, no further attempts.
  const stillRejected = fakeFetch([401, 401, 200]);
  const second = await fetchWithSession(
    fakeBrowserAuth(["old", "new"]),
    "/api/x",
    {},
    {
      workspaceId: "ws-1",
      fetch: stillRejected.fetch,
    },
  );
  assert.equal(second.status, 401);
  assert.equal(stillRejected.requests.length, 2);

  // Renewal impossible (signed out elsewhere): the 401 is returned unretried.
  const noRenewal = fakeFetch([401, 200]);
  const third = await fetchWithSession(
    fakeBrowserAuth(["old"]),
    "/api/x",
    {},
    {
      workspaceId: "ws-1",
      fetch: noRenewal.fetch,
    },
  );
  assert.equal(third.status, 401);
  assert.equal(noRenewal.requests.length, 1);

  // Other failures are not authentication problems and are not retried.
  for (const status of [403, 409, 500]) {
    const other = fakeFetch([status, 200]);
    const auth = fakeBrowserAuth(["token"]);
    const result = await fetchWithSession(
      auth,
      "/api/x",
      {},
      {
        workspaceId: "ws-1",
        fetch: other.fetch,
      },
    );
    assert.equal(result.status, status);
    assert.equal(other.requests.length, 1);
    assert.deepEqual(auth.calls, ["getSession"]);
  }
});

function sourceFiles(directory) {
  const files = [];
  for (const name of readdirSync(directory)) {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) files.push(...sourceFiles(path));
    else if (/\.(ts|tsx|mjs)$/.test(name)) files.push(path);
  }
  return files;
}

test("no client component receives or forwards an access token", () => {
  const offenders = [];
  for (const directory of ["app", "components", "hooks", "lib"]) {
    for (const file of sourceFiles(join(webDir, directory))) {
      const text = readFileSync(file, "utf8");
      const relativePath = file.slice(webDir.length + 1);
      if (/\bapiAccessToken\b/.test(text))
        offenders.push(`${relativePath}: apiAccessToken`);
      if (/^\s*accessToken[?]?:/m.test(text))
        offenders.push(`${relativePath}: accessToken option`);
    }
  }
  assert.deepEqual(offenders, []);
  // The page-load token authenticates only the page's own server-side API
  // calls; it is not handed to a client component for the browser to reuse.
  const projectPage = read("app/(app)/projects/[projectId]/page.tsx");
  const tokenUses = projectPage
    .split("\n")
    .filter((line) => line.includes("access_token"))
    .filter(
      (line) =>
        !line.includes("authorization: `Bearer ${session.access_token}`") &&
        !line.includes("!session?.access_token"),
    );
  assert.deepEqual(tokenUses, []);
  assert.doesNotMatch(projectPage, /\bheaders=\{/);
  const clientApi = read("lib/client-api.ts");
  assert.match(clientApi, /from "@\/lib\/api-session\.mjs"/);
});

test("the unused x-motionly-user-id header is gone from the web app", () => {
  const offenders = [];
  for (const directory of ["app", "components", "hooks", "lib"]) {
    for (const file of sourceFiles(join(webDir, directory))) {
      if (/x-motionly-user-id/i.test(readFileSync(file, "utf8")))
        offenders.push(file);
    }
  }
  assert.deepEqual(offenders, []);
});

// --- the viewer's address for Auth rate limits ---------------------------

test("server-side Auth calls carry the viewer's address set by the ingress", async () => {
  const { VIEWER_IP_HEADER, viewerAddressHeaders } =
    await import("../lib/viewer-address.mjs");
  assert.equal(VIEWER_IP_HEADER, "x-motionly-viewer-ip");
  const from = (value) => ({
    get: (name) => (name === VIEWER_IP_HEADER ? value : null),
  });
  assert.deepEqual(viewerAddressHeaders(from("198.51.100.10")), {
    "x-motionly-viewer-ip": "198.51.100.10",
  });
  assert.deepEqual(viewerAddressHeaders(from("2001:db8::1")), {
    "x-motionly-viewer-ip": "2001:db8::1",
  });
  for (const value of [
    null,
    "",
    "198.51.100.10:443",
    "evil, 1.2.3.4",
    "not-an-ip",
    "1.2.3.4\r\nx: y",
  ]) {
    assert.deepEqual(
      viewerAddressHeaders(from(value)),
      {},
      JSON.stringify(value),
    );
  }
  for (const path of ["proxy.ts", "lib/supabase/server.ts"]) {
    const source = read(path);
    assert.match(source, /from "@\/lib\/viewer-address\.mjs"/, path);
    assert.match(source, /global: \{ headers: viewerAddressHeaders\(/, path);
  }
});
