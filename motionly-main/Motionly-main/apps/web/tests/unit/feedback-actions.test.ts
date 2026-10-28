// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  headersMock,
  navigationMock,
  RedirectError,
  requestHeaders,
  resetNextMocks,
} from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("next/headers", () => headersMock);
vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.internal" } }));

const { submitFeedback } = await import("@/app/feedback/actions");

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

function stubFetch(status = 201) {
  const fetchMock = vi.fn(
    async (_url: string, _init?: RequestInit) => new Response("{}", { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => resetNextMocks());
afterEach(() => vi.unstubAllGlobals());

describe("submitFeedback", () => {
  it("bounds inputs and forwards the viewer's address", async () => {
    requestHeaders.set("x-motionly-viewer-ip", "203.0.113.7");
    const fetchMock = stubFetch();
    expect(
      await redirectOf(() =>
        submitFeedback(
          form({
            category: "<script>",
            message: "  The export button does nothing.  ",
            pageUrl: "https://evil.example/phish",
          }),
        ),
      ),
    ).toBe("/feedback?sent=1");
    const init = (
      fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    )[1];
    expect(JSON.parse(String(init.body))).toEqual({
      category: "general",
      message: "The export button does nothing.",
      page_url: null,
    });
    expect(JSON.stringify(init.headers)).toContain("203.0.113.7");
  });

  it("keeps a same-origin page path", async () => {
    const fetchMock = stubFetch();
    await redirectOf(() =>
      submitFeedback(
        form({ category: "bug", message: "x".repeat(20), pageUrl: "/billing" }),
      ),
    );
    const init = (
      fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    )[1];
    expect(JSON.parse(String(init.body)).page_url).toBe("/billing");
  });

  it("rejects too-short and too-long messages before calling the API", async () => {
    const fetchMock = stubFetch();
    expect(
      await redirectOf(() => submitFeedback(form({ message: "short" }))),
    ).toBe("/feedback?error=message_required");
    expect(
      await redirectOf(() =>
        submitFeedback(form({ message: "x".repeat(5001) })),
      ),
    ).toBe("/feedback?error=message_too_long");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("maps a network failure to an error instead of throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    expect(
      await redirectOf(() =>
        submitFeedback(form({ message: "A long enough message" })),
      ),
    ).toBe("/feedback?error=feedback_failed");
  });
});
