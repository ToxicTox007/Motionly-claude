import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  InspectorTabList,
  inspectorPanelId,
} from "@/components/editor/layout/InspectorTabs";

describe("editor inspector tabs", () => {
  it("points only the selected tab at the rendered panel", () => {
    render(
      <InspectorTabList tab="captions" onTabChange={() => {}} hasActivePlan />,
    );
    const tabs = screen.getAllByRole("tab");
    const selected = screen.getByRole("tab", { selected: true });
    expect(selected).toHaveAccessibleName("Text");
    expect(selected).toHaveAttribute(
      "aria-controls",
      inspectorPanelId("captions"),
    );
    for (const tab of tabs) {
      if (tab === selected) continue;
      expect(tab).not.toHaveAttribute("aria-controls");
    }
  });

  it("names tabs by their label alone and hides the icons", () => {
    const { container } = render(
      <InspectorTabList tab="ai" onTabChange={() => {}} hasActivePlan />,
    );
    expect(screen.getByRole("tab", { selected: true })).toHaveAccessibleName(
      "AI",
    );
    expect(container.textContent).not.toMatch(/Selected:/);
    const icons = container.querySelectorAll("svg");
    expect(icons.length).toBeGreaterThan(0);
    for (const icon of icons) {
      expect(icon).toHaveAttribute("aria-hidden", "true");
    }
  });

  it("arrow keys skip tabs that need an edit plan", async () => {
    const user = userEvent.setup();
    const onTabChange = vi.fn();
    render(
      <InspectorTabList
        tab="look"
        onTabChange={onTabChange}
        hasActivePlan={false}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Look" }));
    onTabChange.mockClear();
    await user.keyboard("{ArrowRight}");
    expect(onTabChange).toHaveBeenCalledWith("scale");
    expect(screen.getByRole("tab", { name: "History" })).toBeDisabled();
  });
});
