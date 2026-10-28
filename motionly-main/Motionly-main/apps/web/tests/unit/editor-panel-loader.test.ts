// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.test" } }));

const { loadEditorPanels } = await import("@/lib/editor-panel-loader");

type Reply = { status: number; body?: unknown; requestId?: string };

function respond(routes: Record<string, Reply>) {
  return vi.fn(async (url: string, _init?: RequestInit) => {
    const path = url.replace("http://api.test", "");
    const reply = routes[path] ?? { status: 200, body: {} };
    return new Response(
      reply.body === undefined ? null : JSON.stringify(reply.body),
      {
        status: reply.status,
        headers: reply.requestId ? { "x-request-id": reply.requestId } : {},
      },
    );
  });
}

const headers = { authorization: "Bearer token" };
const upload = { id: "up-1" };

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("loadEditorPanels", () => {
  it("returns each panel's data when every request succeeds", async () => {
    vi.stubGlobal(
      "fetch",
      respond({
        "/projects/p1/captions": {
          status: 200,
          body: { tracks: [{ id: "t1" }] },
        },
        "/projects/p1/plans": {
          status: 200,
          body: { plans: [], active_plan_id: null },
        },
      }),
    );
    const { data, errors } = await loadEditorPanels("p1", headers, upload);
    expect(errors).toEqual({});
    expect(data.captions).toEqual({ tracks: [{ id: "t1" }] });
    expect(data.plans).toEqual({ plans: [], active_plan_id: null });
  });

  it("marks a panel whose request fails as an error, not as empty", async () => {
    vi.stubGlobal(
      "fetch",
      respond({
        "/projects/p1/captions": {
          status: 500,
          body: { error: { code: "internal_error", request_id: "req-42" } },
        },
      }),
    );
    const { data, errors } = await loadEditorPanels("p1", headers, upload);
    expect(data.captions).toBeNull();
    expect(errors.captions).toEqual({ requestId: "req-42" });
    // One failure does not take the other panels down.
    expect(errors.plans).toBeUndefined();
    expect(data.plans).toEqual({});
  });

  it("treats a timeout or network failure as an error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/broll")) throw new TypeError("fetch failed");
        return new Response("{}", { status: 200 });
      }),
    );
    const { errors } = await loadEditorPanels("p1", headers, upload);
    expect(errors.broll).toEqual({ requestId: null });
  });

  it("a 404 is empty only where nothing-yet is meaningful", async () => {
    vi.stubGlobal(
      "fetch",
      respond({
        "/projects/p1/transcript": { status: 404 },
        "/projects/p1/music/selection": { status: 404 },
        "/projects/p1/effects": { status: 404 },
        "/projects/p1/plans": { status: 404 },
        "/projects/p1/captions": { status: 404 },
      }),
    );
    const { data, errors } = await loadEditorPanels("p1", headers, upload);
    // No transcript, music or look settings yet: genuinely empty.
    expect(data.transcript).toBeNull();
    expect(data.music).toBeNull();
    expect(data.effects).toBeNull();
    expect(errors.transcript).toBeUndefined();
    expect(errors.music).toBeUndefined();
    expect(errors.effects).toBeUndefined();
    // The plans and captions lists always exist for a project: a 404 there
    // is a failure, not "no plans".
    expect(errors.plans).toEqual({ requestId: null });
    expect(errors.captions).toEqual({ requestId: null });
  });

  it("sends the caller's headers to every panel request", async () => {
    const fetchMock = respond({});
    vi.stubGlobal("fetch", fetchMock);
    await loadEditorPanels("p1", headers, upload);
    expect(fetchMock).toHaveBeenCalled();
    for (const [, init] of fetchMock.mock.calls) {
      expect((init?.headers as Record<string, string>).authorization).toBe(
        "Bearer token",
      );
    }
    const paths = fetchMock.mock.calls.map(([url]) =>
      String(url).replace("http://api.test", ""),
    );
    expect(paths).toContain("/projects/p1/uploads/up-1/asset-urls");
    expect(paths).toContain("/billing/status");
  });
});
