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

describe("DashboardClient delete", () => {
  it("confirms first, deletes once and removes the card", async () => {
    const { deleteProjectResult } =
      await import("@/app/(app)/projects/actions");
    const remove = vi.mocked(deleteProjectResult);
    remove.mockClear();
    const projects = ["p1", "p2"].map((id) => ({
      id,
      title: `Reel ${id}`,
      updated_at: id === "p1" ? "2026-10-02T00:00:00Z" : "2026-10-01T00:00:00Z",
      uploaded_videos: [],
    }));
    renderWithProviders(
      <DashboardClient
        initialProjects={projects}
        workspaceName="Acme"
        storage={storage}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Delete Reel p2" }),
    );
    expect(remove).not.toHaveBeenCalled();
    const confirm = screen.getByRole("button", { name: "Delete project" });
    await userEvent.dblClick(confirm);
    expect(remove).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalledWith("p2");
    expect(
      await screen.findByText('"Reel p2" has been deleted.'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Reel p2" }),
    ).not.toBeInTheDocument();
  });
});
