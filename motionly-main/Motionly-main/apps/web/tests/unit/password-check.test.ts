// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({
  env: { supabaseUrl: "http://supabase.internal", supabaseAnonKey: "anon" },
}));
const signInWithPassword = vi.fn();
const signOut = vi.fn(async () => ({ error: null }));
const createClient = vi.fn((..._args: unknown[]) => ({
  auth: { signInWithPassword, signOut },
}));
vi.mock("@supabase/supabase-js", () => ({ createClient }));

const { verifyCurrentPassword } = await import("@/lib/supabase/password-check");

beforeEach(() => {
  signInWithPassword.mockReset();
  signOut.mockClear();
  createClient.mockClear();
});

describe("verifyCurrentPassword", () => {
  it("uses a client that keeps no session and revokes the temporary one", async () => {
    signInWithPassword.mockResolvedValue({
      data: { session: { access_token: "t" } },
      error: null,
    });
    expect(
      await verifyCurrentPassword("a@example.com", "pw", {
        "x-motionly-viewer-ip": "203.0.113.7",
      }),
    ).toBe("ok");
    const options = createClient.mock.calls[0][2] as {
      auth: Record<string, boolean>;
      global: { headers: Record<string, string> };
    };
    expect(options.auth).toEqual({
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    });
    expect(options.global.headers["x-motionly-viewer-ip"]).toBe("203.0.113.7");
    expect(signOut).toHaveBeenCalledWith({ scope: "local" });
  });

  it("maps Auth errors", async () => {
    signInWithPassword.mockResolvedValue({
      data: { session: null },
      error: { code: "invalid_credentials", status: 400 },
    });
    expect(await verifyCurrentPassword("a@example.com", "x", {})).toBe("wrong");
    signInWithPassword.mockResolvedValue({
      data: { session: null },
      error: { code: "over_request_rate_limit", status: 429 },
    });
    expect(await verifyCurrentPassword("a@example.com", "x", {})).toBe(
      "rate_limited",
    );
    signInWithPassword.mockRejectedValue(new TypeError("fetch failed"));
    expect(await verifyCurrentPassword("a@example.com", "x", {})).toBe("error");
  });
});
