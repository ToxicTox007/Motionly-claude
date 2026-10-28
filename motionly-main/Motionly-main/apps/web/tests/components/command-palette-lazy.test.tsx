import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/app/(auth)/actions", () => ({ logout: vi.fn() }));

// Counts evaluations of the panel module: it must not load until the
// palette is first opened.
const panel = vi.hoisted(() => ({ loads: 0 }));
vi.mock("@/components/command-palette-panel", async (importOriginal) => {
  panel.loads += 1;
  return importOriginal();
});

const { CommandPaletteProvider, CommandPaletteTrigger } =
  await import("@/components/command-palette");

beforeEach(() => resetNextMocks());

describe("command palette loading", () => {
  it("does not load the palette panel (cmdk) until it is opened", async () => {
    render(
      <CommandPaletteProvider>
        <CommandPaletteTrigger />
      </CommandPaletteProvider>,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(panel.loads).toBe(0);

    fireEvent.keyDown(document.body, { key: "k", ctrlKey: true });
    expect(
      // The panel module is fetched on first open (cold in tests).
      await screen.findByRole(
        "dialog",
        { name: "Command palette" },
        { timeout: 10_000 },
      ),
    ).toBeInTheDocument();
    expect(panel.loads).toBe(1);
    expect(
      screen.getByRole("combobox", { name: /Command palette|Search/i }),
    ).toBeInTheDocument();
  });
});
