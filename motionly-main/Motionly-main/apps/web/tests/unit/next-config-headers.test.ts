// @vitest-environment node
import { describe, expect, it } from "vitest";
import nextConfig from "../../next.config";

type HeaderRule = {
  source: string;
  headers: Array<{ key: string; value: string }>;
};

async function rules(): Promise<HeaderRule[]> {
  const config = nextConfig("phase-production-build");
  return (await config.headers!()) as HeaderRule[];
}

function headersFor(all: HeaderRule[], path: string) {
  // Later matching rules override earlier ones, as Next applies them.
  const result = new Map<string, string>();
  for (const rule of all) {
    const prefix = rule.source.replace(/:path\*$/, "");
    if (rule.source === path || path.startsWith(prefix)) {
      for (const { key, value } of rule.headers) result.set(key, value);
    }
  }
  return result;
}

describe("security headers", () => {
  it("never caches admin pages and never sends their URLs as a Referer", async () => {
    const admin = headersFor(await rules(), "/admin/users");
    expect(admin.get("Cache-Control")).toBe("no-store");
    expect(admin.get("Referrer-Policy")).toBe("no-referrer");
  });

  it("keeps the site-wide policy elsewhere", async () => {
    const page = headersFor(await rules(), "/dashboard");
    expect(page.get("Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(page.get("Cache-Control")).toBeUndefined();
    expect(page.get("X-Frame-Options")).toBe("DENY");
  });
});
