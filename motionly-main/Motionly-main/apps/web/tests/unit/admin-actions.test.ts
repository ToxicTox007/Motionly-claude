// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  navigationMock,
  RedirectError,
  resetNextMocks,
} from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/env", () => ({
  env: { apiBaseUrl: "http://api.internal", billingMode: "demo" },
}));
const admin = {
  context: { headers: { authorization: "Bearer t" } } as unknown,
};
vi.mock("@/lib/api-auth", () => ({
  getAdminApiContext: vi.fn(async () => admin.context),
}));

const ops = await import("@/app/(app)/admin/ops/actions");
const users = await import("@/app/(app)/admin/users/actions");

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

function stubFetch(status: number) {
  const fetchMock = vi.fn(
    async (_url: string, _init?: RequestInit) => new Response("{}", { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  resetNextMocks();
  admin.context = { headers: { authorization: "Bearer t" } };
});
afterEach(() => vi.unstubAllGlobals());

describe("admin ops actions", () => {
  it("reports success only when the API succeeded", async () => {
    const fetchMock = stubFetch(200);
    expect(
      await redirectOf(() => ops.retryQueueJob(form({ jobId: "job/1" }))),
    ).toBe("/admin/ops?done=retried");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.internal/admin/queues/jobs/job%2F1/retry",
    );
  });

  it("reports a failed cancel instead of looking successful", async () => {
    stubFetch(409);
    expect(
      await redirectOf(() => ops.cancelRenderJob(form({ jobId: "r1" }))),
    ).toBe("/admin/ops?error=cancel_failed");
  });

  it("requires a job id", async () => {
    const fetchMock = stubFetch(200);
    expect(await redirectOf(() => ops.retryRenderJob(form({})))).toBe(
      "/admin/ops?error=job_required",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends a lost admin session to an explained page, not the error screen", async () => {
    admin.context = null;
    expect(await redirectOf(() => ops.deadLetterStuckJobs(form({})))).toBe(
      "/dashboard?error=admin_access_denied",
    );
  });
});

describe("admin user actions", () => {
  it("reports a failed ban", async () => {
    stubFetch(500);
    expect(
      await redirectOf(() =>
        users.setUserBan(
          form({ userId: "u1", email: "a@example.com", banned: "true" }),
        ),
      ),
    ).toBe("/admin/users?q=a%40example.com&error=ban_failed");
  });

  it("reports a successful unban", async () => {
    stubFetch(200);
    expect(
      await redirectOf(() =>
        users.setUserBan(
          form({ userId: "u1", email: "a@example.com", banned: "false" }),
        ),
      ),
    ).toBe("/admin/users?q=a%40example.com&done=unbanned");
  });

  it("reports a failed dismiss", async () => {
    stubFetch(500);
    expect(
      await redirectOf(() =>
        users.dismissResetRequest(form({ requestId: "r1" })),
      ),
    ).toBe("/admin/users?error=dismiss_failed");
  });
});
