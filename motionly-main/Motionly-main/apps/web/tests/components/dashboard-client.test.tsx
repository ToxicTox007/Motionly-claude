import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks, router } from "../setup/next-mocks";
import { renderWithProviders } from "../setup/render";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/app/(app)/projects/actions", () => ({
  deleteProjectResult: vi.fn(async () => ({ ok: true })),
}));

const { DashboardClient } = await import("@/components/DashboardClient");

const storage = {
  metered: false,
  label: "Not metered",
  percent: null,
  percentLabel: null,
  ariaLabel: "Storage",
};

beforeEach(() => {
  resetNextMocks();
});

describe("DashboardClient", () => {
  it("shows a load error with Retry, not the first-run onboarding", async () => {
    renderWithProviders(
      <DashboardClient
        initialProjects={[]}
        loadError
        workspaceName="Acme"
        storage={storage}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load your projects",
    );
    expect(screen.queryByText("No projects yet")).not.toBeInTheDocument();
    expect(screen.queryByText("Upload Video")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("shows onboarding only when the list genuinely is empty", () => {
    renderWithProviders(
      <DashboardClient
        initialProjects={[]}
        workspaceName="Acme"
        storage={storage}
      />,
    );
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("adopts refreshed server data", () => {
    const project = {
      id: "p1",
      title: "Launch reel",
      updated_at: "2026-10-01T00:00:00Z",
      uploaded_videos: [],
    };
    const { rerender } = renderWithProviders(
      <DashboardClient
        initialProjects={[]}
        workspaceName="Acme"
        storage={storage}
      />,
    );
    rerender(
      <DashboardClient
        initialProjects={[project]}
        workspaceName="Acme"
        storage={storage}
      />,
    );
    expect(
      screen.getAllByRole("link", { name: "Launch reel" }).length,
    ).toBeGreaterThan(0);
  });
});
