// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  cookieJar,
  cookieStore,
  headersMock,
  navigationMock,
  RedirectError,
  resetNextMocks,
} from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("next/headers", () => headersMock);
vi.mock("@/lib/env", () => ({
  env: {
    skipAuthMiddleware: false,
    appUrl: "https://app.example.com",
    apiBaseUrl: "http://api.internal",
  },
}));
vi.mock("@/lib/api-auth", () => ({
  WORKSPACE_COOKIE_NAME: "motionly_workspace_id",
}));
const auth = {
  signInWithPassword: vi.fn(),
  signOut: vi.fn(async () => ({ error: null })),
};
vi.mock("@/lib/supabase/server", () => ({
  createClient: vi.fn(async () => ({ auth })),
}));

const { login, logout } = await import("@/app/(auth)/actions");

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

beforeEach(() => {
  resetNextMocks();
  auth.signInWithPassword.mockReset();
  auth.signOut.mockClear();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("login", () => {
  it("keeps the destination when the password is wrong", async () => {
    auth.signInWithPassword.mockResolvedValue({
      error: { code: "invalid_credentials", status: 400, message: "x" },
    });
    expect(
      await redirectOf(() =>
        login(form({ email: "a@example.com", password: "x", next: "/invite" })),
      ),
    ).toBe("/login?error=invalid_credentials&next=%2Finvite");
  });

  it("keeps the destination on a system error, and still rejects off-site next", async () => {
    auth.signInWithPassword.mockRejectedValue(new Error("down"));
    expect(
      await redirectOf(() =>
        login(
          form({
            email: "a@example.com",
            password: "x",
            next: "/projects/p1?tab=export",
          }),
        ),
      ),
    ).toBe("/login?error=system_error&next=%2Fprojects%2Fp1%3Ftab%3Dexport");
    auth.signInWithPassword.mockResolvedValue({
      error: { code: "invalid_credentials", status: 400, message: "x" },
    });
    expect(
      await redirectOf(() =>
        login(
          form({
            email: "a@example.com",
            password: "x",
            next: "https://evil.example/",
          }),
        ),
      ),
    ).toBe("/login?error=invalid_credentials");
  });

  it("goes to the destination on success", async () => {
    auth.signInWithPassword.mockResolvedValue({ error: null });
    expect(
      await redirectOf(() =>
        login(
          form({ email: "a@example.com", password: "x", next: "/billing" }),
        ),
      ),
    ).toBe("/billing");
  });
});

describe("logout", () => {
  it("signs out and forgets the selected workspace", async () => {
    cookieJar.set("motionly_workspace_id", "w1");
    expect(await redirectOf(() => logout())).toBe("/login");
    expect(auth.signOut).toHaveBeenCalled();
    expect(cookieStore.delete).toHaveBeenCalledWith("motionly_workspace_id");
    expect(cookieJar.has("motionly_workspace_id")).toBe(false);
  });
});
