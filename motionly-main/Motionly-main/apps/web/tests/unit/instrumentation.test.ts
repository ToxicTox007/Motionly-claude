import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The standalone server (output: "standalone") does not run next.config.ts
// when it starts, so instrumentation.ts keeps the fail-fast contract (FE-42):
// a misconfigured container exits non-zero, naming the variable, before it
// serves a request.

const VALID = {
  APP_ENV: "test",
  PUBLIC_ORIGIN: "http://127.0.0.1:3000",
  API_INTERNAL_URL: "http://127.0.0.1:8000",
  SUPABASE_URL: "http://127.0.0.1:54321",
  SUPABASE_ANON_KEY: "test-anon-key",
};

const { register } = await import("@/instrumentation");

let exit: ReturnType<typeof vi.spyOn>;
let error: ReturnType<typeof vi.spyOn>;

function environment(values: Record<string, string>) {
  for (const [name, value] of Object.entries(values)) vi.stubEnv(name, value);
}

beforeEach(() => {
  exit = vi
    .spyOn(process, "exit")
    .mockImplementation((() => undefined) as never);
  error = vi.spyOn(console, "error").mockImplementation(() => {});
  environment({ ...VALID, NEXT_RUNTIME: "nodejs", NEXT_PHASE: "" });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("instrumentation register()", () => {
  it("lets a configured server start", async () => {
    await register();
    expect(exit).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
  });

  it("exits non-zero, naming the missing variable", async () => {
    environment({ PUBLIC_ORIGIN: "" });
    await register();
    expect(exit).toHaveBeenCalledWith(1);
    expect(String(error.mock.calls[0]?.[0])).toMatch(
      /configuration.*PUBLIC_ORIGIN is required/i,
    );
  });

  it("exits non-zero on an invalid value", async () => {
    environment({ API_INTERNAL_URL: "ftp://api" });
    await register();
    expect(exit).toHaveBeenCalledWith(1);
    expect(String(error.mock.calls[0]?.[0])).toMatch(/API_INTERNAL_URL/);
  });

  it("does nothing while building, or in the edge runtime", async () => {
    environment({ PUBLIC_ORIGIN: "" });
    vi.stubEnv("NEXT_PHASE", "phase-production-build");
    await register();
    vi.stubEnv("NEXT_PHASE", "");
    vi.stubEnv("NEXT_RUNTIME", "edge");
    await register();
    expect(exit).not.toHaveBeenCalled();
  });
});
