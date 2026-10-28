import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/app/(auth)/actions", () => ({ logout: vi.fn() }));
vi.mock("@/components/command-palette", () => ({
  RegisterCommands: () => null,
  CommandPaletteTrigger: () => <button type="button">Search commands</button>,
}));
vi.mock("@/components/landing/hero-console", () => ({
  HeroConsole: () => <div />,
}));
vi.mock("@/components/landing/reveal", () => ({
  Reveal: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/lib/env", () => ({ env: { skipAuthMiddleware: true } }));
vi.mock("@/lib/api-auth", () => ({
  WORKSPACE_COOKIE_NAME: "motionly_workspace_id",
  getActiveMemberships: vi.fn(),
  getSessionSnapshot: vi.fn(),
}));
vi.mock("@/lib/server-api", () => ({ serverApiFetch: vi.fn() }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined }),
}));

const { AppShell } = await import("@/components/app-shell");
const { default: HomePage } = await import("@/app/page");

beforeEach(() => resetNextMocks());

describe("page landmarks", () => {
  it("app shell: skip link, banner with labelled navs, main holding the h1", async () => {
    render(await AppShell({ title: "Billing", children: <p>Body</p> }));
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveAttribute("href", "#main");
    const main = screen.getByRole("main");
    expect(main).toHaveAttribute("id", "main");
    expect(
      within(main).getByRole("heading", { level: 1, name: "Billing" }),
    ).toBeInTheDocument();
    const banner = screen.getByRole("banner");
    expect(
      within(banner).getByRole("navigation", { name: "Workspace" }),
    ).toBeInTheDocument();
    expect(
      within(banner).getByRole("navigation", { name: "Settings" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    // The logo is decorative next to the wordmark.
    expect(screen.queryByRole("img", { name: "Motionly" })).toBeNull();
  });

  it("landing: header, main and footer are siblings, with a skip link", () => {
    render(<HomePage />);
    const banner = screen.getByRole("banner");
    const main = screen.getByRole("main");
    const footer = screen.getByRole("contentinfo");
    expect(main).not.toContainElement(banner);
    expect(main).not.toContainElement(footer);
    expect(
      within(banner).getByRole("navigation", { name: "Main" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Skip to content" }),
    ).toHaveAttribute("href", "#main");
    expect(within(main).getByRole("heading", { level: 1 })).toBeInTheDocument();
    // The home link is named once, not "Motionly Motionly".
    expect(
      within(banner).getAllByRole("link", { name: "Motionly" }).length,
    ).toBe(1);
  });
});
