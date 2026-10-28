import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.test" } }));
vi.mock("@/lib/supabase/browser", () => ({
  createClient: () => ({ auth: {} }),
}));
vi.mock("@/lib/api-session.mjs", () => ({
  fetchWithSession: (_auth: unknown, input: string, init: RequestInit) =>
    fetch(input, init),
}));

const { editorApiRequest } = await import("@/lib/client-api");

const BODY = { caption: "Private caption text from the user" };
const PAYLOAD = {
  error: {
    code: "plan_validation_failed",
    message: "Caption 'Private caption text from the user' is too long",
    request_id: "req-9",
  },
};

let errorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(PAYLOAD), { status: 422 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

async function failingRequest() {
  await expect(
    editorApiRequest("http://api.test/projects/p1/captions?q=secret#x", {
      workspaceId: "ws-1",
      method: "PATCH",
      body: BODY,
    }),
  ).rejects.toThrow();
}

describe("editorApiRequest failure logging", () => {
  it("logs only the path, method, status, code and request id", async () => {
    await failingRequest();
    expect(errorSpy).toHaveBeenCalledTimes(1);
    const logged = JSON.stringify(errorSpy.mock.calls[0]);
    expect(logged).not.toContain("Private caption text");
    expect(logged).not.toContain("secret");
    expect(errorSpy.mock.calls[0][1]).toEqual({
      path: "http://api.test/projects/p1/captions",
      method: "PATCH",
      status: 422,
      code: "plan_validation_failed",
      requestId: "req-9",
    });
  });

  it("does not log at all in production builds", async () => {
    vi.stubEnv("NODE_ENV", "production");
    await failingRequest();
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
