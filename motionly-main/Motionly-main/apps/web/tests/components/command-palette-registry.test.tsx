import { Profiler } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/app/(auth)/actions", () => ({ logout: vi.fn() }));

const { CommandPaletteProvider, RegisterCommands } =
  await import("@/components/command-palette");

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Rendered outside act() so React schedules work as it does in a browser: an
// update loop then shows up as a commit count instead of hanging act().
const actEnvironment = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
let previousActEnvironment: boolean | undefined;
let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  resetNextMocks();
  previousActEnvironment = actEnvironment.IS_REACT_ACT_ENVIRONMENT;
  actEnvironment.IS_REACT_ACT_ENVIRONMENT = false;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  root.unmount();
  container.remove();
  actEnvironment.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
});

describe("command palette registry", () => {
  // Registering commands used to change the registry context, which re-ran
  // every registering effect (unregister, register, ...) without end. The
  // signed-in shell then committed tens of thousands of times a second, and
  // client-side navigations and server-action redirects never completed.
  it("settles after a page registers its commands", async () => {
    let commits = 0;
    root.render(
      <Profiler id="palette" onRender={() => (commits += 1)}>
        <CommandPaletteProvider>
          <RegisterCommands
            source="app-shell"
            commands={[
              {
                id: "nav-projects",
                label: "Projects",
                group: "Navigate",
                href: "/projects",
              },
            ]}
          />
        </CommandPaletteProvider>
      </Profiler>,
    );
    await wait(150);
    const settled = commits;
    await wait(150);
    expect(settled).toBeGreaterThan(0);
    expect(settled).toBeLessThan(10);
    expect(commits - settled).toBe(0);
  });
});
