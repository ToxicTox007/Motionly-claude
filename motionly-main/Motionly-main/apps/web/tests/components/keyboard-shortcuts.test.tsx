import {
  act,
  createEvent,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TimelineClip } from "@motionly/shared";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/app/(auth)/actions", () => ({ logout: vi.fn() }));

const { useTimelineKeyboardShortcuts } =
  await import("@/components/editor/timeline/useTimelineKeyboardShortcuts");
const { CommandPaletteProvider } = await import("@/components/command-palette");
const { Modal } = await import("@/components/modal");

const clip: TimelineClip = {
  id: "clip-1",
  trackKind: "video",
  startMs: 0,
  endMs: 4000,
  label: "Intro",
  colorClass: "bg-primary",
  disabled: false,
  sourceRef: { kind: "cut", index: 0 },
};

type HarnessProps = {
  playheadMs: number;
  togglePlayback: (mode: "pause" | "forward" | "backward") => void;
  trimSelectedClip: (
    clip: TimelineClip,
    side: "left" | "right",
    playheadMs: number,
  ) => void;
  dialogOpen?: boolean;
};

function Harness({ dialogOpen = false, ...props }: HarnessProps) {
  useTimelineKeyboardShortcuts({ ...props, selectedClip: clip });
  return (
    <div>
      <div
        data-testid="notes"
        contentEditable
        suppressContentEditableWarning
        tabIndex={0}
      >
        Notes
      </div>
      <button type="button">Play</button>
      <Modal open={dialogOpen} title="Confirm" onClose={() => {}}>
        <button type="button">Inside</button>
      </Modal>
    </div>
  );
}

function setup(overrides: Partial<HarnessProps> = {}) {
  const props: HarnessProps = {
    playheadMs: 1000,
    togglePlayback: vi.fn(),
    trimSelectedClip: vi.fn(),
    ...overrides,
  };
  const view = render(<Harness {...props} />);
  return { props, ...view };
}

beforeEach(() => resetNextMocks());

describe("timeline keyboard shortcuts", () => {
  it("J, K and L control playback from the page", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    await user.click(screen.getByRole("button", { name: "Play" }));
    await user.keyboard("jkl");
    expect(props.togglePlayback).toHaveBeenNthCalledWith(1, "backward");
    expect(props.togglePlayback).toHaveBeenNthCalledWith(2, "pause");
    expect(props.togglePlayback).toHaveBeenNthCalledWith(3, "forward");
  });

  it("typing k in a contenteditable region does not pause playback", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    await user.click(screen.getByTestId("notes"));
    await user.keyboard("k");
    expect(props.togglePlayback).not.toHaveBeenCalled();
  });

  it("leaves browser chords like Ctrl+L alone", () => {
    const { props } = setup();
    const event = createEvent.keyDown(document.body, {
      key: "l",
      ctrlKey: true,
    });
    fireEvent(document.body, event);
    expect(event.defaultPrevented).toBe(false);
    expect(props.togglePlayback).not.toHaveBeenCalled();
  });

  it("does nothing while a dialog is open", () => {
    const { props } = setup({ dialogOpen: true });
    fireEvent.keyDown(document.body, { key: "k" });
    fireEvent.keyDown(screen.getByRole("button", { name: "Inside" }), {
      key: "k",
    });
    expect(props.togglePlayback).not.toHaveBeenCalled();
  });

  it("registers one listener and trims at the latest playhead", () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    const { props, rerender } = setup();
    for (const playheadMs of [1500, 2000, 2500]) {
      rerender(<Harness {...props} playheadMs={playheadMs} />);
    }
    const keydownListeners = addSpy.mock.calls.filter(
      ([type]) => type === "keydown",
    );
    expect(keydownListeners).toHaveLength(1);

    fireEvent.keyDown(document.body, { key: "]" });
    expect(props.trimSelectedClip).toHaveBeenCalledWith(clip, "right", 2500);
  });
});

describe("command palette shortcut", () => {
  function renderPalette() {
    return render(
      <CommandPaletteProvider>
        <input aria-label="Title" />
        <textarea aria-label="Notes" />
        <div
          data-testid="rich"
          contentEditable
          suppressContentEditableWarning
          tabIndex={0}
        >
          Rich text
        </div>
      </CommandPaletteProvider>,
    );
  }

  it("Ctrl+K opens the palette from a single-line input", async () => {
    renderPalette();
    const input = screen.getByRole("textbox", { name: "Title" });
    act(() => input.focus());
    fireEvent.keyDown(input, { key: "k", ctrlKey: true });
    // The palette panel loads on first open.
    expect(
      await screen.findByRole("dialog", {}, { timeout: 10_000 }),
    ).toBeInTheDocument();
  });

  it("Ctrl+K in a textarea or contenteditable keeps its native meaning", () => {
    renderPalette();
    const textarea = screen.getByRole("textbox", { name: "Notes" });
    const textareaEvent = createEvent.keyDown(textarea, {
      key: "k",
      ctrlKey: true,
    });
    fireEvent(textarea, textareaEvent);
    const rich = screen.getByTestId("rich");
    const richEvent = createEvent.keyDown(rich, { key: "k", metaKey: true });
    fireEvent(rich, richEvent);
    expect(textareaEvent.defaultPrevented).toBe(false);
    expect(richEvent.defaultPrevented).toBe(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
