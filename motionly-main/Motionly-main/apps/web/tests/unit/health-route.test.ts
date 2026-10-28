// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.internal" } }));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.resetModules();
});

describe("GET /app-api/health", () => {
  it("reuses the upstream readiness answer for a few seconds", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-28T00:00:00Z"));
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("@/app/app-api/health/route");

    const first = await GET();
    await GET();
    await GET();
    expect(first.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    vi.setSystemTime(new Date("2026-10-28T00:00:06Z"));
    await GET();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reports unreachable without leaking the private URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("connect ECONNREFUSED api.internal");
      }),
    );
    const { GET } = await import("@/app/app-api/health/route");
    const response = await GET();
    expect(response.status).toBe(503);
    const text = await response.text();
    expect(text).not.toContain("api.internal");
    expect(JSON.parse(text)).toEqual({
      status: "degraded",
      service: "web",
      api: { status: "unreachable" },
    });
  });
});
