// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.internal" } }));
const admin = { context: { headers: {} } as unknown };
vi.mock("@/lib/api-auth", () => ({
  getAdminApiContext: vi.fn(async () => admin.context),
}));

const { createResetLink } = await import("@/app/(app)/admin/users/actions");
const { revalidatePath } = await import("next/cache");

const IDLE = { status: "idle" } as const;
const LINK =
  "https://app.example.com/auth/confirm#token_hash=secret-one-time-token&type=recovery";

function form(values: Record<string, string>) {
  const data = new FormData();
  for (const [key, value] of Object.entries(values)) data.set(key, value);
  return data;
}

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn(
    async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  resetNextMocks();
  admin.context = { headers: {} };
});
afterEach(() => vi.unstubAllGlobals());

describe("createResetLink", () => {
  it("returns the link as action state and never redirects with it", async () => {
    const fetchMock = stubFetch({ link: LINK });
    const state = await createResetLink(
      IDLE,
      form({ email: "a@example.com", requestId: "req-1" }),
    );
    expect(state).toEqual({ status: "ok", link: LINK });
    expect(navigationMock.redirect).not.toHaveBeenCalled();
    // The list is not revalidated under the admin's feet (the panel refreshes
    // it when closed), so the link cannot unmount before it is copied.
    expect(revalidatePath).not.toHaveBeenCalled();
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      email: "a@example.com",
      request_id: "req-1",
    });
  });

  it("returns a mapped error code on failure", async () => {
    stubFetch({ error: { code: "user_not_found" } }, 404);
    expect(
      await createResetLink(IDLE, form({ email: "nobody@example.com" })),
    ).toEqual({ status: "error", error: "user_not_found" });
    expect(navigationMock.redirect).not.toHaveBeenCalled();
  });

  it("requires an email and an admin session", async () => {
    expect(await createResetLink(IDLE, form({ email: " " }))).toEqual({
      status: "error",
      error: "email_required",
    });
    admin.context = null;
    expect(
      await createResetLink(IDLE, form({ email: "a@example.com" })),
    ).toEqual({ status: "error", error: "admin_access_denied" });
  });
});
