// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.internal/" } }));

const { serverApiFetch, failureCode } = await import("@/lib/server-api");

function respond(body: string | null, init: ResponseInit = {}) {
  return vi.fn(async () => new Response(body, init));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("serverApiFetch", () => {
  it("returns parsed JSON for a 2xx response", async () => {
    const fetchMock = respond(JSON.stringify({ projects: [{ id: "p1" }] }), {
      status: 200,
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await serverApiFetch<{ projects: { id: string }[] }>(
      "/projects",
      { headers: { authorization: "Bearer t" } },
    );
    expect(result).toEqual({
      ok: true,
      status: 200,
      data: { projects: [{ id: "p1" }] },
    });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("http://api.internal/projects");
    expect(init.cache).toBe("no-store");
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(init.headers).toEqual({ authorization: "Bearer t" });
  });

  it("returns null data for an empty 204 body", async () => {
    vi.stubGlobal("fetch", respond(null, { status: 204 }));
    expect(await serverApiFetch("/thing", { method: "DELETE" })).toEqual({
      ok: true,
      status: 204,
      data: null,
    });
  });

  it("sends a JSON body with a content type", async () => {
    const fetchMock = respond("{}", { status: 201 });
    vi.stubGlobal("fetch", fetchMock);
    await serverApiFetch("/projects", {
      method: "POST",
      body: { title: "A" },
    });
    const init = (
      fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    )[1];
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"title":"A"}');
    expect(init.headers).toEqual({ "content-type": "application/json" });
  });

  it("reads the error envelope of a JSON error response", async () => {
    vi.stubGlobal(
      "fetch",
      respond(
        JSON.stringify({
          error: { code: "plan_limit", message: "x", request_id: "req_1" },
        }),
        { status: 402 },
      ),
    );
    expect(await serverApiFetch("/billing/checkout")).toEqual({
      ok: false,
      kind: "http",
      status: 402,
      code: "plan_limit",
      requestId: "req_1",
    });
  });

  it("treats an HTML error page as an http failure with no code", async () => {
    vi.stubGlobal(
      "fetch",
      respond("<html>Bad gateway</html>", { status: 502 }),
    );
    expect(await serverApiFetch("/projects")).toEqual({
      ok: false,
      kind: "http",
      status: 502,
    });
  });

  it("treats invalid JSON in a 2xx response as a parse failure", async () => {
    vi.stubGlobal("fetch", respond("{not json", { status: 200 }));
    expect(await serverApiFetch("/projects")).toEqual({
      ok: false,
      kind: "parse",
      status: 200,
    });
  });

  it("maps a rejected fetch to a network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    expect(await serverApiFetch("/projects")).toEqual({
      ok: false,
      kind: "network",
    });
  });

  it("maps an aborted request to a timeout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal?.addEventListener("abort", () =>
              reject(new DOMException("timed out", "TimeoutError")),
            );
          }),
      ),
    );
    expect(await serverApiFetch("/projects", { timeoutMs: 5 })).toEqual({
      ok: false,
      kind: "timeout",
    });
  });

  it("rejects absolute URLs so callers cannot reach other hosts", async () => {
    await expect(serverApiFetch("https://evil.example/x")).rejects.toThrow(
      /API path/,
    );
  });
});

describe("failureCode", () => {
  it("keeps a safe API code and replaces anything else", () => {
    expect(
      failureCode({ ok: false, kind: "http", code: "plan_limit" }, "x"),
    ).toBe("plan_limit");
    expect(
      failureCode({ ok: false, kind: "http", code: "<script>" }, "fallback"),
    ).toBe("fallback");
    expect(failureCode({ ok: false, kind: "timeout" }, "x")).toBe("timeout");
    expect(failureCode({ ok: false, kind: "network" }, "x")).toBe(
      "network_error",
    );
  });
});
