import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks, router } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);

const { PanelLoadError } = await import("@/components/editor/PanelLoadError");
const { InspectorTabList } =
  await import("@/components/editor/layout/InspectorTabs");
const { failedPanelsForTab } = await import("@/lib/editor-panels");

beforeEach(() => resetNextMocks());

describe("editor panel load errors", () => {
  it("says which panel failed and retries by reloading the editor data", async () => {
    const user = userEvent.setup();
    render(
      <PanelLoadError
        panels={["captions"]}
        errors={{ captions: { requestId: "req-42" } }}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Couldn't load captions" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/req-42/)).toBeInTheDocument();
    expect(screen.queryByText(/Generate an AI edit/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("names several failed panels together", () => {
    render(
      <PanelLoadError
        panels={["renders", "exports"]}
        errors={{ renders: { requestId: null }, exports: { requestId: null } }}
      />,
    );
    expect(
      screen.getByRole("heading", {
        name: "Couldn't load renders and exports",
      }),
    ).toBeInTheDocument();
  });

  it("maps each tab to the data it cannot work without", () => {
    const errors = { captions: { requestId: null } };
    expect(failedPanelsForTab("captions", errors)).toEqual(["captions"]);
    expect(failedPanelsForTab("localize", errors)).toEqual(["captions"]);
    expect(failedPanelsForTab("look", errors)).toEqual([]);
    expect(
      failedPanelsForTab("history", { plans: { requestId: null } }),
    ).toEqual(["plans"]);
  });

  it("tabs that need a plan explain a failed plan load instead of asking for an AI edit", () => {
    render(
      <InspectorTabList
        tab="ai"
        onTabChange={() => {}}
        hasActivePlan={false}
        plansUnavailable
      />,
    );
    const history = screen.getByRole("tab", { name: "History" });
    expect(history).toBeDisabled();
    expect(history).toHaveAttribute("title", "Edit plans couldn't be loaded");
  });
});
