import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({
  env: { apiBaseUrl: "http://api.test", appUrl: "http://app.test" },
}));

const { PublicReviewClient } =
  await import("@/app/review/[shareId]/PublicReviewClient");
const { WatchClient } = await import("@/app/watch/WatchClient");

const review = {
  id: "rev-1",
  edit_plan_revision: 3,
  source_media_revision: 1,
  plan_hash: "h",
  status: "open",
  status_revision: 1,
  decision_reason: null,
  decided_at: null,
  created_at: "2026-10-01T00:00:00Z",
  comments: [],
  can_comment: true,
  can_decide: false,
  expires_at: "2026-11-01T00:00:00Z",
  media_url: null,
  media_expires_at: null,
};

function visit(path: string) {
  window.history.replaceState(null, "", path);
}

const fetchMock = vi.fn(
  async (_url: string, _init?: RequestInit) =>
    new Response(JSON.stringify(review), { status: 200 }),
);

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  visit("/");
});

describe("public review page", () => {
  it("a malformed link says so instead of spinning forever", async () => {
    visit("/review/share-1#%E0%A4%A");
    render(<PublicReviewClient shareId="share-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /This review link is invalid/,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a link without its token says the token is missing", async () => {
    visit("/review/share-1");
    render(<PublicReviewClient shareId="share-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /missing its access token/,
    );
  });

  it("encodes the share id, sends the token in the body and clears it from the URL", async () => {
    visit("/review/a%2Fb#secret-token");
    render(<PublicReviewClient shareId="a/b" />);
    expect(
      await screen.findByRole("heading", { name: "Revision review" }),
    ).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/public/reviews/a%2Fb/resolve");
    expect(JSON.parse(String(init?.body))).toEqual({ token: "secret-token" });
    expect(window.location.hash).toBe("");
  });

  it("keeps working after a reload in the same tab", async () => {
    visit("/review/share-1#secret-token");
    const first = render(<PublicReviewClient shareId="share-1" />);
    await screen.findByRole("heading", { name: "Revision review" });
    first.unmount();
    expect(window.location.hash).toBe("");
    render(<PublicReviewClient shareId="share-1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      token: "secret-token",
    });
  });

  it("shows friendly copy for a network failure, not 'Failed to fetch'", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    visit("/review/share-1#secret-token");
    render(<PublicReviewClient shareId="share-1" />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/couldn't reach Motionly/i);
    expect(alert).not.toHaveTextContent(/Failed to fetch/);
  });
});

describe("watch page", () => {
  const exportPayload = {
    title: "Launch",
    version: 2,
    width: 1080,
    height: 1920,
    fps: 30,
    media_url: "https://media.test/v.mp4",
    allow_download: false,
    download_url: null,
    download_filename: null,
  };

  it("clears the token from the URL once it is read", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(exportPayload), { status: 200 }),
    );
    visit("/watch#t=watch-token");
    render(<WatchClient />);
    expect(
      await screen.findByRole("heading", { name: "Launch" }),
    ).toBeInTheDocument();
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      token: "watch-token",
    });
    expect(window.location.hash).toBe("");
  });

  it("a link without a token says so", async () => {
    visit("/watch#t=");
    render(<WatchClient />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /missing its access token/,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows friendly copy for a network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    visit("/watch#t=watch-token");
    render(<WatchClient />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/couldn't reach Motionly/i);
    expect(alert).not.toHaveTextContent(/Failed to fetch/);
  });
});
