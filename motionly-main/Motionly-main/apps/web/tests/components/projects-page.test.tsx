import { screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";
import { renderWithProviders } from "../setup/render";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/app/(app)/projects/actions", () => ({
  updateProject: vi.fn(),
  deleteProjectResult: vi.fn(),
}));
vi.mock("@/lib/api-auth", () => ({
  getWorkspaceApiContext: vi.fn(async () => ({
    headers: { authorization: "Bearer t" },
  })),
}));
const api = vi.hoisted(() => ({
  serverApiFetch: vi.fn(),
}));
vi.mock("@/lib/server-api", () => api);

const { default: ProjectsPage } = await import("@/app/(app)/projects/page");

function project(index: number) {
  return {
    id: `p${index}`,
    title: `Reel ${index}`,
    status: "draft",
    updated_at: "2026-10-01T00:00:00Z",
    created_at: "2026-09-01T00:00:00Z",
  };
}

async function renderPage(searchParams: Record<string, string>) {
  renderWithProviders(
    await ProjectsPage({ searchParams: Promise.resolve(searchParams) }),
  );
}

beforeEach(() => {
  resetNextMocks();
  api.serverApiFetch.mockReset();
});

describe("projects page search, sort and paging", () => {
  it("asks the API for the requested page, search and sort", async () => {
    api.serverApiFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: { projects: [project(25)], total: 1 },
    });
    await renderPage({ q: "reel 25", sort: "title_asc", page: "1" });
    expect(api.serverApiFetch).toHaveBeenCalledWith(
      "/projects?limit=20&offset=0&sort=title_asc&q=reel+25",
      { headers: { authorization: "Bearer t" } },
    );
    expect(
      screen.getByRole("searchbox", { name: "Search projects" }),
    ).toHaveValue("reel 25");
    expect(screen.getByRole("combobox", { name: "Sort" })).toHaveValue(
      "title_asc",
    );
    expect(screen.getByText(/1 project matches “reel 25”/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("Reel 25")).toBeInTheDocument();
  });

  it("searches with a GET form so the query lives in the URL", async () => {
    api.serverApiFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: { projects: [project(1)], total: 1 },
    });
    await renderPage({});
    const form = screen.getByRole("search");
    expect(form).toHaveAttribute("action", "/projects");
    expect(form).toHaveAttribute("method", "get");
  });

  it("shows pagination that keeps the search and sort", async () => {
    api.serverApiFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        projects: Array.from({ length: 20 }, (_, index) => project(index + 21)),
        total: 57,
      },
    });
    await renderPage({ q: "reel", sort: "title_asc", page: "2" });
    expect(api.serverApiFetch).toHaveBeenCalledWith(
      "/projects?limit=20&offset=20&sort=title_asc&q=reel",
      expect.anything(),
    );
    const nav = screen.getByRole("navigation", { name: "Pagination" });
    expect(within(nav).getByRole("link", { name: "Page 2" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      within(nav).getByRole("link", { name: "Next page" }),
    ).toHaveAttribute("href", "/projects?q=reel&sort=title_asc&page=3");
    expect(screen.getByText(/57 projects match “reel”/)).toBeInTheDocument();
  });

  it("tells an empty search apart from having no projects", async () => {
    api.serverApiFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: { projects: [], total: 0 },
    });
    await renderPage({ q: "nothing" });
    expect(screen.getByText("No projects match “nothing”")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Clear search" })).toHaveAttribute(
      "href",
      "/projects",
    );
    expect(screen.queryByText("No projects yet")).toBeNull();
  });

  it("offers the last page when the requested page is past the end", async () => {
    api.serverApiFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: { projects: [], total: 25 },
    });
    await renderPage({ page: "9" });
    expect(
      screen.getByRole("link", { name: "Go to the last page" }),
    ).toHaveAttribute("href", "/projects?page=2");
  });

  it("shows a load error with Retry when the API fails", async () => {
    api.serverApiFetch.mockResolvedValue({ ok: false, kind: "network" });
    await renderPage({});
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load your projects",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
