import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.test" } }));
vi.mock("@/lib/supabase/browser", () => ({
  createClient: () => ({ auth: {} }),
}));
vi.mock("@/lib/api-session.mjs", () => ({
  fetchWithSession: (_auth: unknown, input: string, init: RequestInit) =>
    fetch(input, init),
}));

const { editorApiRequest, RevisionConflictError } =
  await import("@/lib/client-api");

function reply(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  );
}

async function request() {
  return editorApiRequest("http://api.test/projects/p1/plans/x/cuts", {
    workspaceId: "ws-1",
    method: "PATCH",
    body: { cuts: [] },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("editorApiRequest revision conflicts", () => {
  it("turns the standard 409 envelope into a RevisionConflictError", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    reply(409, {
      error: { code: "collaboration_revision_conflict", request_id: "r-1" },
      expected_revision: 3,
      current_revision: 5,
    });
    const error = await request().catch((caught) => caught);
    expect(error).toBeInstanceOf(RevisionConflictError);
    expect(error).toMatchObject({
      expectedRevision: 3,
      currentRevision: 5,
      requestId: "r-1",
      status: 409,
    });
  });

  it("still accepts the legacy flat conflict string", async () => {
    reply(409, {
      error: "revision_conflict",
      expected_revision: 1,
      current_revision: 2,
    });
    const error = await request().catch((caught) => caught);
    expect(error).toBeInstanceOf(RevisionConflictError);
    expect(error).toMatchObject({ expectedRevision: 1, currentRevision: 2 });
  });

  it("does not treat other 409s as revision conflicts", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    reply(409, { error: { code: "slug_taken", message: "Taken" } });
    const error = await request().catch((caught) => caught);
    expect(error).not.toBeInstanceOf(RevisionConflictError);
  });
});
