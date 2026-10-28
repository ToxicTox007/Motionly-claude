import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  navigationMock,
  navigationState,
  resetNextMocks,
} from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/app/(auth)/actions", () => ({ logout: vi.fn() }));

const { MobileNav } = await import("@/components/mobile-nav");

const items = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/projects", label: "Projects" },
  { href: "/projects/new", label: "New Video" },
];

beforeEach(() => resetNextMocks());

describe("MobileNav", () => {
  it("has a stable name and reports its state", async () => {
    render(<MobileNav items={items} />);
    const trigger = screen.getByRole("button", { name: "Menu" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const dialog = screen.getByRole("dialog", { name: "Navigation menu" });
    expect(trigger).toHaveAttribute("aria-controls", dialog.id);
  });

  it("marks only the most specific current page", async () => {
    navigationState.pathname = "/projects/new";
    render(<MobileNav items={items} />);
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    const dialog = screen.getByRole("dialog", { name: "Navigation menu" });
    expect(
      within(dialog).getByRole("link", { name: "New Video" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      within(dialog).getByRole("link", { name: "Projects" }),
    ).not.toHaveAttribute("aria-current");
  });

  it("closes when the route changes", async () => {
    const { rerender } = render(<MobileNav items={items} />);
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    act(() => {
      navigationState.pathname = "/projects";
    });
    rerender(<MobileNav items={items} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers the workspace switcher, usage and log out to signed-in viewers", async () => {
    render(
      <MobileNav
        items={items}
        account={{
          workspaces: [
            { id: "w1", name: "Acme" },
            { id: "w2", name: "Studio" },
          ],
          currentWorkspaceId: "w1",
          usageLabel: "3 / 30 renders",
        }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    const dialog = screen.getByRole("dialog", { name: "Navigation menu" });
    expect(within(dialog).getByLabelText("Workspace")).toHaveValue("w1");
    expect(within(dialog).getByText("3 / 30 renders")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Log out" }),
    ).toBeInTheDocument();
  });
});

describe("WorkspaceSwitcher", () => {
  it("does not switch on change and returns to the current page", async () => {
    navigationState.pathname = "/billing";
    const { WorkspaceSwitcher } =
      await import("@/components/workspace-switcher");
    const submit = vi.fn((event: Event) => event.preventDefault());
    const { container } = render(
      <WorkspaceSwitcher
        workspaces={[
          { id: "w1", name: "Acme" },
          { id: "w2", name: "Studio" },
        ]}
        currentWorkspaceId="w1"
      />,
    );
    container.querySelector("form")!.addEventListener("submit", submit);
    await userEvent.selectOptions(screen.getByLabelText("Workspace"), "w2");
    expect(submit).not.toHaveBeenCalled();
    expect(container.querySelector('input[name="return_to"]')).toHaveAttribute(
      "value",
      "/billing",
    );
    await userEvent.click(screen.getByRole("button", { name: "Switch" }));
    expect(submit).toHaveBeenCalledTimes(1);
  });
});
