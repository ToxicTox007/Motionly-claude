// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.internal" } }));

const { loadDashboardData, DASHBOARD_PROJECT_LIMIT } =
  await import("@/lib/dashboard-data");

type Route = { status: number; body: unknown };

function stubApi(routes: Record<string, Route>) {
  const fetchMock = vi.fn(async (url: string) => {
    const path = new URL(url).pathname;
    const route = routes[path];
    if (!route) return new Response("not found", { status: 404 });
    return new Response(JSON.stringify(route.body), { status: route.status });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const quota = { used_bytes: 50, max_bytes: 100, percentage: 50 };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("loadDashboardData", () => {
  it("makes one projects call and one quota call, never one per upload", async () => {
    const fetchMock = stubApi({
      "/projects": {
        status: 200,
        body: {
          projects: [
            {
              id: "p1",
              title: "Launch reel",
              updated_at: "2026-10-01T00:00:00Z",
              latest_upload: {
                id: "u1",
                status: "ready",
                original_filename: "clip.mp4",
                duration_ms: 61000,
                warnings: ["low audio"],
              },
            },
          ],
        },
      },
      "/storage/quota": { status: 200, body: quota },
    });
    const data = await loadDashboardData({ authorization: "Bearer t" });
    const paths = fetchMock.mock.calls.map(
      ([url]) => new URL(url as string).pathname,
    );
    expect(paths.sort()).toEqual(["/projects", "/storage/quota"]);
    expect(data.loadError).toBe(false);
    expect(data.projects[0]).toEqual({
      id: "p1",
      title: "Launch reel",
      updated_at: "2026-10-01T00:00:00Z",
      uploaded_videos: [
        {
          status: "ready",
          original_filename: "clip.mp4",
          duration_ms: 61000,
          metadata: { warnings: ["low audio"] },
        },
      ],
    });
    expect(data.storage.metered).toBe(true);
  });

  it("reports a load error instead of an empty list when the API fails", async () => {
    stubApi({
      "/projects": { status: 500, body: { error: { code: "internal" } } },
      "/storage/quota": { status: 200, body: quota },
    });
    const data = await loadDashboardData({});
    expect(data.loadError).toBe(true);
    expect(data.projects).toEqual([]);
  });

  it("treats a malformed projects payload as a load error", async () => {
    stubApi({
      "/projects": { status: 200, body: { items: [] } },
      "/storage/quota": { status: 200, body: quota },
    });
    expect((await loadDashboardData({})).loadError).toBe(true);
  });

  it("degrades a failed quota to not metered without failing the page", async () => {
    stubApi({
      "/projects": { status: 200, body: { projects: [] } },
      "/storage/quota": { status: 503, body: {} },
    });
    const data = await loadDashboardData({});
    expect(data.loadError).toBe(false);
    expect(data.storage.metered).toBe(false);
  });

  it("caps the dashboard at the first page of projects", async () => {
    stubApi({
      "/projects": {
        status: 200,
        body: {
          projects: Array.from({ length: 20 }, (_, i) => ({
            id: `p${i}`,
            title: `P${i}`,
            updated_at: "2026-10-01T00:00:00Z",
          })),
        },
      },
      "/storage/quota": { status: 200, body: quota },
    });
    const data = await loadDashboardData({});
    expect(data.projects).toHaveLength(DASHBOARD_PROJECT_LIMIT);
  });

  it("asks for the latest page only and reports the workspace total", async () => {
    const fetchMock = stubApi({
      "/projects": {
        status: 200,
        body: {
          projects: [
            { id: "p1", title: "P1", updated_at: "2026-10-01T00:00:00Z" },
          ],
          total: 30,
        },
      },
      "/storage/quota": { status: 200, body: quota },
    });
    const data = await loadDashboardData({});
    const projectsUrl = fetchMock.mock.calls
      .map(([url]) => new URL(url as string))
      .find((url) => url.pathname === "/projects");
    expect(projectsUrl?.searchParams.get("limit")).toBe(
      String(DASHBOARD_PROJECT_LIMIT),
    );
    expect(projectsUrl?.searchParams.get("sort")).toBe("updated_desc");
    expect(data.total).toBe(30);
  });

  it("falls back to the page size when an older API omits the total", async () => {
    stubApi({
      "/projects": {
        status: 200,
        body: {
          projects: [
            { id: "p1", title: "P1", updated_at: "2026-10-01T00:00:00Z" },
          ],
        },
      },
      "/storage/quota": { status: 200, body: quota },
    });
    expect((await loadDashboardData({})).total).toBe(1);
  });
});
