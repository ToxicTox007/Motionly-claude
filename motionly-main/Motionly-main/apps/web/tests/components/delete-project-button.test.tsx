import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks, router } from "../setup/next-mocks";
import { renderWithProviders } from "../setup/render";

vi.mock("next/navigation", () => navigationMock);
const deleteProjectResult = vi.fn();
vi.mock("@/app/(app)/projects/actions", () => ({
  deleteProjectResult: (...args: unknown[]) => deleteProjectResult(...args),
}));

const { DeleteProjectButton } =
  await import("@/app/(app)/projects/delete-project-button");

beforeEach(() => {
  resetNextMocks();
  deleteProjectResult.mockReset();
});

describe("DeleteProjectButton", () => {
  it("is named after the project and asks before deleting", async () => {
    deleteProjectResult.mockResolvedValue({ ok: true });
    renderWithProviders(
      <DeleteProjectButton projectId="p1" title="Launch reel" />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Delete Launch reel" }),
    );
    expect(deleteProjectResult).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "Delete project?" }),
    ).toHaveTextContent("Launch reel");

    await userEvent.click(
      screen.getByRole("button", { name: "Delete project" }),
    );
    expect(deleteProjectResult).toHaveBeenCalledWith("p1");
    await waitFor(() => expect(router.refresh).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps the project and explains a failure", async () => {
    deleteProjectResult.mockResolvedValue({
      ok: false,
      error: "network_error",
    });
    renderWithProviders(
      <DeleteProjectButton projectId="p1" title="Launch reel" />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Delete Launch reel" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Delete project" }),
    );
    expect(await screen.findByText(/could not reach/i)).toBeInTheDocument();
    expect(router.refresh).not.toHaveBeenCalled();
  });
});
