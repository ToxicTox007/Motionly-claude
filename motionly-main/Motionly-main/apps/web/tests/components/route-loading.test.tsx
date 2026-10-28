import { render, screen } from "@testing-library/react";
import type { ComponentType, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

const shell = vi.hoisted(() => ({ titles: [] as string[] }));
vi.mock("@/components/app-shell", () => ({
  AppShell: ({ title, children }: { title: string; children: ReactNode }) => {
    shell.titles.push(title);
    return (
      <div>
        <h1>{title}</h1>
        {children}
      </div>
    );
  },
}));

const routes: Array<{
  path: string;
  title: string;
  label: RegExp;
  load: () => Promise<{ default: ComponentType }>;
}> = [
  {
    path: "admin/loading",
    title: "Admin",
    label: /Loading admin/,
    load: () => import("@/app/(app)/admin/loading"),
  },
  {
    path: "account/team/loading",
    title: "Team",
    label: /Loading team/,
    load: () => import("@/app/(app)/account/team/loading"),
  },
  {
    path: "account/api-keys/loading",
    title: "API Keys",
    label: /Loading API keys/,
    load: () => import("@/app/(app)/account/api-keys/loading"),
  },
  {
    path: "account/ai-models/loading",
    title: "AI models",
    label: /Loading AI models/,
    load: () => import("@/app/(app)/account/ai-models/loading"),
  },
  {
    path: "account/api-reference/loading",
    title: "API Reference",
    label: /Loading the API reference/,
    load: () => import("@/app/(app)/account/api-reference/loading"),
  },
  {
    path: "projects/new/loading",
    title: "Create New Reel",
    label: /Loading the new project form/,
    load: () => import("@/app/(app)/projects/new/loading"),
  },
];

describe("route loading screens", () => {
  it.each(routes)(
    "$path keeps the page title and announces loading",
    async ({ title, label, load }) => {
      const { default: Loading } = await load();
      const { container } = render(<Loading />);
      expect(
        screen.getByRole("heading", { level: 1, name: title }),
      ).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent(label);
      // Shimmer blocks are decorative and hidden from assistive tech.
      const blocks = container.querySelectorAll(".neu-shimmer");
      expect(blocks.length).toBeGreaterThan(3);
      for (const block of blocks) {
        expect(block.closest('[aria-hidden="true"]')).not.toBeNull();
      }
      expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
    },
  );

  it("the admin skeleton is a table, matching the admin list pages", async () => {
    const { default: Loading } = await import("@/app/(app)/admin/loading");
    const { container } = render(<Loading />);
    expect(container.querySelector('[data-skeleton="table"]')).not.toBeNull();
  });

  it("the new project skeleton is a form, not the projects list", async () => {
    const { default: Loading } =
      await import("@/app/(app)/projects/new/loading");
    const { container } = render(<Loading />);
    expect(container.querySelector('[data-skeleton="form"]')).not.toBeNull();
    expect(container.querySelector('[data-skeleton="table"]')).toBeNull();
  });
});
