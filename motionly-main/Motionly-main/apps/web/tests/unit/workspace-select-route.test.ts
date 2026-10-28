// @vitest-environment node
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { isSameSiteRequest } from "@/lib/request-origin";

vi.mock("@/lib/env", () => ({
  env: { appUrl: "https://app.example.com" },
}));
const supabase = {
  user: { id: "u1" } as { id: string } | null,
  membership: { workspace_id: "w2" } as { workspace_id: string } | null,
};
vi.mock("@/lib/supabase/server", () => ({
  createClient: vi.fn(async () => ({
    auth: { getUser: async () => ({ data: { user: supabase.user } }) },
    from: () => {
      const query = {
        select: () => query,
        eq: () => query,
        maybeSingle: async () => ({ data: supabase.membership, error: null }),
      };
      return query;
    },
  })),
}));
vi.mock("@/lib/api-auth", () => ({
  WORKSPACE_COOKIE_NAME: "motionly_workspace_id",
}));

const { POST } = await import("@/app/app-api/workspaces/select/route");

function post(fields: Record<string, string>, headers: Record<string, string>) {
  const body = new URLSearchParams(fields);
  return new NextRequest("http://localhost:3000/app-api/workspaces/select", {
    method: "POST",
    body,
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      host: "localhost:3000",
      ...headers,
    },
  });
}

beforeEach(() => {
  supabase.user = { id: "u1" };
  supabase.membership = { workspace_id: "w2" };
});

describe("isSameSiteRequest", () => {
  const origin = "https://app.example.com";
  const headers = (values: Record<string, string>) => new Headers(values);

  it("accepts this site's Origin or the addressed host", () => {
    expect(
      isSameSiteRequest(headers({ origin: "https://app.example.com" }), origin),
    ).toBe(true);
    expect(
      isSameSiteRequest(
        headers({ origin: "http://127.0.0.1:3000", host: "127.0.0.1:3000" }),
        origin,
      ),
    ).toBe(true);
  });

  it("refuses another site, a sibling subdomain and an opaque origin", () => {
    for (const value of [
      "https://evil.example",
      "https://other.app.example.com",
      "null",
      "not a url",
    ]) {
      expect(
        isSameSiteRequest(
          headers({ origin: value, host: "app.example.com" }),
          origin,
        ),
        value,
      ).toBe(false);
    }
  });

  it("falls back to Sec-Fetch-Site without an Origin", () => {
    expect(
      isSameSiteRequest(headers({ "sec-fetch-site": "cross-site" }), origin),
    ).toBe(false);
    expect(
      isSameSiteRequest(headers({ "sec-fetch-site": "same-origin" }), origin),
    ).toBe(true);
  });
});

describe("POST /app-api/workspaces/select", () => {
  it("pins the workspace and returns to the page it was switched from", async () => {
    const response = await POST(
      post(
        { workspace_id: "w2", return_to: "/billing" },
        { origin: "https://app.example.com" },
      ),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "https://app.example.com/billing",
    );
    expect(response.cookies.get("motionly_workspace_id")?.value).toBe("w2");
  });

  it("never follows an off-site return address", async () => {
    const response = await POST(
      post(
        { workspace_id: "w2", return_to: "https://evil.example/x" },
        { origin: "https://app.example.com" },
      ),
    );
    expect(response.headers.get("location")).toBe(
      "https://app.example.com/dashboard",
    );
  });

  it("refuses a cross-site form post without touching the cookie", async () => {
    const response = await POST(
      post({ workspace_id: "w2" }, { origin: "https://evil.example" }),
    );
    expect(response.status).toBe(403);
    expect(response.cookies.get("motionly_workspace_id")).toBeUndefined();
  });

  it("refuses a workspace the viewer does not belong to", async () => {
    supabase.membership = null;
    const response = await POST(
      post(
        { workspace_id: "w9", return_to: "/billing" },
        { origin: "https://app.example.com" },
      ),
    );
    expect(response.headers.get("location")).toBe(
      "https://app.example.com/dashboard?error=workspace_invalid",
    );
    expect(response.cookies.get("motionly_workspace_id")).toBeUndefined();
  });
});
