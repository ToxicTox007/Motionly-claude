// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  navigationMock,
  RedirectError,
  resetNextMocks,
} from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.internal" } }));
vi.mock("@/lib/api-auth", () => ({
  getWorkspaceApiContext: vi.fn(async () => ({
    role: "owner",
    headers: { authorization: "Bearer t" },
  })),
}));

const { createProject, updateProject, deleteProjectResult } =
  await import("@/app/(app)/projects/actions");

function form(values: Record<string, string>) {
  const data = new FormData();
  for (const [key, value] of Object.entries(values)) data.set(key, value);
  return data;
}

async function redirectOf(run: () => Promise<unknown>) {
  try {
    await run();
  } catch (error) {
    if (error instanceof RedirectError) return error.location;
    throw error;
  }
  throw new Error("expected a redirect");
}

function stubFetch(body: string | null, status = 200) {
  const fetchMock = vi.fn(
    async (_url: string, _init?: RequestInit) => new Response(body, { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => resetNextMocks());
afterEach(() => vi.unstubAllGlobals());

describe("updateProject", () => {
  it("refuses a cleared title instead of silently keeping the old one", async () => {
    const fetchMock = stubFetch("{}");
    expect(
      await redirectOf(() =>
        updateProject(form({ projectId: "p1", title: "   " })),
      ),
    ).toBe("/projects?error=title_required");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses an unknown status", async () => {
    stubFetch("{}");
    expect(
      await redirectOf(() =>
        updateProject(form({ projectId: "p1", title: "A", status: "done" })),
      ),
    ).toBe("/projects?error=status_invalid");
  });

  it("encodes the project id into the API path", async () => {
    const fetchMock = stubFetch(JSON.stringify({ id: "p1" }));
    expect(
      await redirectOf(() =>
        updateProject(form({ projectId: "../admin", title: "A" })),
      ),
    ).toBe("/projects?updated=1");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.internal/projects/..%2Fadmin",
    );
  });

  it("maps an HTML 502 to an error instead of throwing", async () => {
    stubFetch("<html>bad gateway</html>", 502);
    expect(
      await redirectOf(() =>
        updateProject(form({ projectId: "p1", title: "A" })),
      ),
    ).toBe("/projects?error=update_failed");
  });
});

describe("createProject", () => {
  it("opens the new project on success", async () => {
    stubFetch(JSON.stringify({ id: "p9" }), 201);
    expect(await redirectOf(() => createProject(form({ title: "Reel" })))).toBe(
      "/projects/p9",
    );
  });

  it("keeps the viewer on the form with a mapped error on failure", async () => {
    stubFetch("", 500);
    expect(await redirectOf(() => createProject(form({ title: "Reel" })))).toBe(
      "/projects/new?error=create_failed",
    );
  });
});

describe("deleteProjectResult", () => {
  it("reports network failures and unexpected answers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    expect(await deleteProjectResult("p1")).toEqual({
      ok: false,
      error: "network_error",
    });
    stubFetch(JSON.stringify({ deleted: false }));
    expect(await deleteProjectResult("p1")).toEqual({
      ok: false,
      error: "delete_failed",
    });
    stubFetch(JSON.stringify({ deleted: true }));
    expect(await deleteProjectResult("p1")).toEqual({ ok: true });
  });
});
