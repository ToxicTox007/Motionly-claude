// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  headersMock,
  navigationMock,
  RedirectError,
  resetNextMocks,
} from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("next/headers", () => headersMock);
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/env", () => ({ env: { skipAuthMiddleware: false } }));

const auth = {
  updateUser: vi.fn(),
  signOut: vi.fn(async () => ({ error: null })),
};
const rpc = vi.fn();
vi.mock("@/lib/api-auth", () => ({
  getSessionSnapshot: vi.fn(async () => ({
    supabase: { auth, rpc },
    user: { id: "u1", email: "creator@example.com" },
  })),
}));
const verifyCurrentPassword = vi.fn();
vi.mock("@/lib/supabase/password-check", () => ({
  verifyCurrentPassword: (...args: unknown[]) => verifyCurrentPassword(...args),
}));

const { updatePassword, updateProfile } =
  await import("@/app/(app)/account/actions");

const STRONG = "Plum-orchard-42";

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

const change = (overrides: Record<string, string> = {}) =>
  redirectOf(() =>
    updatePassword(
      form({
        currentPassword: "old-Password-1",
        password: STRONG,
        confirmPassword: STRONG,
        ...overrides,
      }),
    ),
  );

beforeEach(() => {
  resetNextMocks();
  auth.updateUser.mockReset().mockResolvedValue({ error: null });
  auth.signOut.mockClear();
  verifyCurrentPassword.mockReset().mockResolvedValue("ok");
  rpc.mockReset();
});

describe("updatePassword", () => {
  it("requires the current password", async () => {
    expect(await change({ currentPassword: "" })).toBe(
      "/account?error=current_password_required",
    );
    expect(auth.updateUser).not.toHaveBeenCalled();
  });

  it("reports a mismatch as a mismatch", async () => {
    expect(await change({ confirmPassword: "something-else-9" })).toBe(
      "/account?error=password_mismatch",
    );
  });

  it("applies the shared password policy (not just a length check)", async () => {
    expect(
      await change({ password: "password1", confirmPassword: "password1" }),
    ).toBe("/account?error=password_weak");
    expect(
      await change({
        password: "creator-2026",
        confirmPassword: "creator-2026",
      }),
    ).toBe("/account?error=password_weak");
    expect(verifyCurrentPassword).not.toHaveBeenCalled();
  });

  it("rejects a wrong current password before changing anything", async () => {
    verifyCurrentPassword.mockResolvedValue("wrong");
    expect(await change()).toBe("/account?error=current_password_wrong");
    expect(auth.updateUser).not.toHaveBeenCalled();
    verifyCurrentPassword.mockResolvedValue("rate_limited");
    expect(await change()).toBe("/account?error=rate_limited");
  });

  it("maps Auth's refusals to fixed codes", async () => {
    auth.updateUser.mockResolvedValue({
      error: { code: "same_password", status: 422 },
    });
    expect(await change()).toBe("/account?error=same_password");
    auth.updateUser.mockResolvedValue({
      error: { code: "weak_password", status: 422 },
    });
    expect(await change()).toBe("/account?error=password_weak");
    auth.updateUser.mockResolvedValue({
      error: { code: "unexpected", status: 500 },
    });
    expect(await change()).toBe("/account?error=system_error");
  });

  it("changes the password and ends every other session", async () => {
    expect(await change()).toBe("/account?password=1");
    expect(verifyCurrentPassword).toHaveBeenCalledWith(
      "creator@example.com",
      "old-Password-1",
      expect.any(Object),
    );
    expect(auth.updateUser).toHaveBeenCalledWith({ password: STRONG });
    expect(auth.signOut).toHaveBeenCalledWith({ scope: "others" });
  });
});

describe("updateProfile", () => {
  it("never puts a raw database code in the URL", async () => {
    rpc.mockResolvedValue({ error: { code: "42501" } });
    expect(await redirectOf(() => updateProfile(form({ fullName: "A" })))).toBe(
      "/account?error=profile_update_failed",
    );
  });
});
