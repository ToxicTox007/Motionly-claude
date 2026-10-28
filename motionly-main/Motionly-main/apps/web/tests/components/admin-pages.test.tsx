import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/lib/api-auth", () => ({
  getAdminApiContext: vi.fn(async () => ({ headers: {} })),
}));
vi.mock("@/lib/server-api", () => ({ serverApiFetch: vi.fn() }));
vi.mock("@/app/(app)/admin/ai/AdminAiClient", () => ({
  AdminAiClient: () => <div>AI client</div>,
}));

const { serverApiFetch } = await import("@/lib/server-api");
const { default: AdminMusicPage } =
  await import("@/app/(app)/admin/music/page");
const { default: AdminAiPage } = await import("@/app/(app)/admin/ai/page");

const fetchMock = vi.mocked(serverApiFetch);

beforeEach(() => resetNextMocks());
afterEach(() => fetchMock.mockReset());

describe("admin music page", () => {
  it("says the catalog is unavailable instead of 'no tracks' and an allow-list warning", async () => {
    fetchMock.mockResolvedValue({ ok: false, kind: "http", status: 500 });
    render(await AdminMusicPage());
    expect(
      screen.getByText("The music catalog is unavailable"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No catalog tracks yet/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/allow-list is not marked/),
    ).not.toBeInTheDocument();
  });

  it("shows the empty state for a genuinely empty catalog", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      data: { tracks: [], allow_list_approved: true },
    });
    render(await AdminMusicPage());
    expect(screen.getByText(/No catalog tracks yet/)).toBeInTheDocument();
  });
});

describe("admin AI page", () => {
  it("tells an unreachable API apart from a disabled registry", async () => {
    fetchMock.mockResolvedValue({ ok: false, kind: "network" });
    render(await AdminAiPage());
    expect(
      screen.getByText("The AI configuration is unavailable"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/is not enabled/)).not.toBeInTheDocument();
  });

  it("explains the disabled registry when the API says so", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      data: { database_enabled: false },
    });
    render(await AdminAiPage());
    expect(
      screen.getByText("The database inference registry is not enabled"),
    ).toBeInTheDocument();
  });
});
