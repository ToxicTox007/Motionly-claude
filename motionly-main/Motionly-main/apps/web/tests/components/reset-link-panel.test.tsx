import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks, router } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
const LINK = "https://app.example.com/auth/confirm#token_hash=t&type=recovery";
const createResetLink = vi.fn();
vi.mock("@/app/(app)/admin/users/actions", () => ({
  createResetLink: (...args: unknown[]) => createResetLink(...args),
}));

const { ResetLinkPanel } =
  await import("@/app/(app)/admin/users/ResetLinkPanel");

beforeEach(() => {
  resetNextMocks();
  createResetLink.mockReset();
  window.history.replaceState(null, "", "/admin/users?q=a%40example.com");
});

describe("ResetLinkPanel", () => {
  it("shows the link once, keeps it out of the URL, and forgets it when closed", async () => {
    createResetLink.mockResolvedValue({ status: "ok", link: LINK });
    render(<ResetLinkPanel email="a@example.com" />);

    await userEvent.click(
      screen.getByRole("button", { name: "Create reset link" }),
    );
    const panel = await screen.findByRole("group", {
      name: "One-time reset link for a@example.com",
    });
    expect(panel).toHaveTextContent(LINK);
    await waitFor(() => expect(panel).toHaveFocus());
    expect(window.location.href).not.toContain("token_hash");
    expect(window.location.href).not.toContain("link=");

    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText(LINK)).not.toBeInTheDocument();
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("shows mapped copy for a failure, never the raw code", async () => {
    createResetLink.mockResolvedValue({
      status: "error",
      error: "user_not_found",
    });
    render(<ResetLinkPanel email="nobody@example.com" />);
    await userEvent.click(
      screen.getByRole("button", { name: "Create reset link" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No account uses that email.",
    );
  });
});
