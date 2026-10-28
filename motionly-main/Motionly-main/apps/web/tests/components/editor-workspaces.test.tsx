import { act, fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EditorWorkspaces } from "@/components/editor/layout/EditorWorkspaces";
import type {
  CreatorFlow,
  EditorMode,
  EditorPanels,
} from "@/components/editor/layout/types";

const mounts: Record<string, number> = {};

function Panel({ name }: { name: string }) {
  const [clicks, setClicks] = useState(0);
  useEffect(() => {
    mounts[name] = (mounts[name] ?? 0) + 1;
  }, [name]);
  return (
    <button type="button" onClick={() => setClicks((value) => value + 1)}>
      {name} clicked {clicks}
    </button>
  );
}

const panels: EditorPanels = {
  leftPanel: <Panel name="left" />,
  centerPanel: <Panel name="center" />,
  rightPanel: <Panel name="right" />,
  bottomPanel: <Panel name="bottom" />,
};

const creatorFlow: CreatorFlow = {
  tasks: [
    {
      key: "captions",
      label: "Captions",
      shortLabel: "Captions",
      description: "Style your captions",
      status: "active",
    },
  ],
  activeTaskKey: "captions",
  progressPct: 20,
  workflowStatusLabel: "Workflow: ready",
  workflowStatusDetail: "",
  onTaskSelect: () => {},
};

function workspaces(mode: EditorMode) {
  return (
    <EditorWorkspaces mode={mode} creatorFlow={creatorFlow} panels={panels} />
  );
}

function setViewport(desktop: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: query === "(min-width: 1024px)" ? desktop : false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

afterEach(() => {
  for (const key of Object.keys(mounts)) delete mounts[key];
});

describe("EditorWorkspaces", () => {
  it("hydrates a desktop editor without mounting the mobile tree first", async () => {
    setViewport(true);
    const container = document.createElement("div");
    // The server cannot know the viewport.
    container.innerHTML = renderToString(workspaces("professional"));
    expect(container.querySelector("[data-editor-workspace]")).toBeNull();
    expect(container.textContent).not.toMatch(/clicked/);
    document.body.append(container);

    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    await act(async () => {
      render(workspaces("professional"), { container, hydrate: true });
    });
    expect(errors).not.toHaveBeenCalled();

    expect(
      container.querySelector('[data-editor-workspace="professional"]'),
    ).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Settings" })).toBeNull();
    // Each panel mounted exactly once: no mobile-then-desktop remount.
    expect(mounts).toEqual({ left: 1, center: 1, right: 1, bottom: 1 });
  });

  it("creator mode shows the current task and no element palette", () => {
    setViewport(true);
    const { container } = render(workspaces("creator"));
    expect(
      container.querySelector('[data-editor-workspace="creator"]'),
    ).not.toBeNull();
    expect(screen.getByText("Captions")).toBeInTheDocument();
    expect(screen.queryByText(/left clicked/)).toBeNull();
    for (const name of ["center", "right", "bottom"]) {
      expect(
        screen.getByRole("button", { name: `${name} clicked 0` }),
      ).toBeInTheDocument();
    }
  });

  it("toggling the mode on desktop keeps panel state", () => {
    setViewport(true);
    const { rerender } = render(workspaces("creator"));
    fireEvent.click(screen.getByRole("button", { name: "right clicked 0" }));
    fireEvent.click(screen.getByRole("button", { name: "bottom clicked 0" }));
    rerender(workspaces("professional"));
    expect(
      screen.getByRole("button", { name: "right clicked 1" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "bottom clicked 1" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/left clicked/)).toBeInTheDocument();
    rerender(workspaces("creator"));
    expect(
      screen.getByRole("button", { name: "right clicked 1" }),
    ).toBeInTheDocument();
    expect(mounts.right).toBe(1);
    expect(mounts.center).toBe(1);
    expect(mounts.bottom).toBe(1);
  });

  it("toggling the mode on mobile keeps panel state", () => {
    setViewport(false);
    const { rerender } = render(workspaces("professional"));
    fireEvent.click(screen.getByRole("button", { name: "center clicked 0" }));
    rerender(workspaces("creator"));
    expect(
      screen.getByRole("button", { name: "center clicked 1" }),
    ).toBeInTheDocument();
    expect(mounts.center).toBe(1);
  });

  it("closes the element sheet when switching to creator mode on mobile", () => {
    setViewport(false);
    const { rerender } = render(workspaces("professional"));
    fireEvent.click(screen.getByRole("button", { name: "Elements" }));
    expect(
      screen.getByRole("dialog", { name: "Element palette" }),
    ).toBeInTheDocument();
    rerender(workspaces("creator"));
    expect(
      screen.queryByRole("dialog", { name: "Element palette" }),
    ).toBeNull();
  });
});
