import { DndContext } from "@dnd-kit/core";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { TimelineClip } from "@motionly/shared";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ClipBlock,
  NUDGE_COMMIT_DELAY_MS,
  type ClipBlockProps,
} from "@/components/editor/timeline/ClipBlock";

const brollClip: TimelineClip = {
  id: "clip-1",
  trackKind: "broll",
  startMs: 5000,
  endMs: 8000,
  label: "Skyline",
  colorClass: "bg-info",
  disabled: false,
  sourceRef: { kind: "broll_slot", id: "slot-1" },
};

function setup(overrides: Partial<ClipBlockProps> = {}) {
  const props: ClipBlockProps = {
    clip: brollClip,
    pct: (ms) => `${ms / 100}%`,
    selected: true,
    onSelect: vi.fn(),
    onTrim: vi.fn(),
    onMove: vi.fn(),
    onReorder: vi.fn(),
    // 1px = 10ms, no snapping, so the numbers below are easy to follow.
    msFromPointer: (clientX) => clientX * 10,
    trimDisabled: false,
    moveDisabled: false,
    reorderDisabled: true,
    getSnappedMs: (ms) => ms,
    setSnapMarkerMs: vi.fn(),
    setDragPreview: vi.fn(),
    ...overrides,
  };
  const view = render(
    <DndContext>
      <ClipBlock {...props} />
    </DndContext>,
  );
  const clip = screen.getByRole("button", { name: /broll clip Skyline/ });
  const [leftHandle, rightHandle] = screen.getAllByTitle("Trim clip");
  return { props, view, clip, leftHandle, rightHandle };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("ClipBlock pointer drags", () => {
  it("commits a trim exactly once on pointerup", () => {
    const { props, rightHandle } = setup();
    fireEvent.pointerDown(rightHandle, { pointerId: 1, clientX: 800 });
    fireEvent.pointerMove(rightHandle, { pointerId: 1, clientX: 900 });
    expect(props.setDragPreview).toHaveBeenLastCalledWith({
      clipId: "clip-1",
      startMs: 5000,
      endMs: 9000,
    });
    fireEvent.pointerUp(rightHandle, { pointerId: 1, clientX: 900 });
    fireEvent.pointerUp(rightHandle, { pointerId: 1, clientX: 950 });
    expect(props.onTrim).toHaveBeenCalledTimes(1);
    expect(props.onTrim).toHaveBeenCalledWith(brollClip, "right", 9000);
    expect(props.setDragPreview).toHaveBeenLastCalledWith(null);
  });

  it("a cancelled trim (e.g. the browser took the touch for scrolling) never commits", () => {
    const { props, leftHandle } = setup();
    fireEvent.pointerDown(leftHandle, { pointerId: 2, clientX: 500 });
    fireEvent.pointerMove(leftHandle, { pointerId: 2, clientX: 300 });
    fireEvent.pointerCancel(leftHandle, { pointerId: 2, clientX: 300 });
    expect(props.setDragPreview).toHaveBeenLastCalledWith(null);
    expect(props.setSnapMarkerMs).toHaveBeenLastCalledWith(null);
    // The next tap anywhere must not apply the abandoned drag.
    fireEvent.pointerUp(leftHandle, { pointerId: 2, clientX: 300 });
    fireEvent.pointerUp(document.body, { pointerId: 2, clientX: 300 });
    expect(props.onTrim).not.toHaveBeenCalled();
  });

  it("losing pointer capture also abandons the drag", () => {
    const { props, clip } = setup();
    fireEvent.pointerDown(clip, { pointerId: 3, clientX: 500 });
    fireEvent.pointerMove(clip, { pointerId: 3, clientX: 700 });
    fireEvent(
      clip,
      new PointerEvent("lostpointercapture", { bubbles: true, pointerId: 3 }),
    );
    fireEvent.pointerUp(clip, { pointerId: 3, clientX: 700 });
    expect(props.onMove).not.toHaveBeenCalled();
    expect(props.setDragPreview).toHaveBeenLastCalledWith(null);
  });

  it("commits a move once on pointerup and ignores other pointers", () => {
    const { props, clip } = setup();
    fireEvent.pointerDown(clip, { pointerId: 4, clientX: 500 });
    fireEvent.pointerUp(clip, { pointerId: 9, clientX: 900 });
    expect(props.onMove).not.toHaveBeenCalled();
    fireEvent.pointerMove(clip, { pointerId: 4, clientX: 700 });
    fireEvent.pointerUp(clip, { pointerId: 4, clientX: 700 });
    expect(props.onMove).toHaveBeenCalledTimes(1);
    expect(props.onMove).toHaveBeenCalledWith(brollClip, 7000);
  });

  it("drags without window listeners and clears the preview when unmounted mid-drag", () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    const { props, view, rightHandle } = setup();
    fireEvent.pointerDown(rightHandle, { pointerId: 5, clientX: 800 });
    fireEvent.pointerMove(rightHandle, { pointerId: 5, clientX: 850 });
    view.unmount();
    const pointerListeners = addSpy.mock.calls.filter(([type]) =>
      String(type).startsWith("pointer"),
    );
    expect(pointerListeners).toHaveLength(0);
    expect(props.setDragPreview).toHaveBeenLastCalledWith(null);
    expect(props.onTrim).not.toHaveBeenCalled();
  });

  it("trim handles opt out of touch scrolling so a drag is not hijacked", () => {
    const { leftHandle, rightHandle } = setup();
    expect(leftHandle).toHaveClass("touch-none");
    expect(rightHandle).toHaveClass("touch-none");
  });

  it("snaps to a boundary within range and shows the snap marker", () => {
    const getSnappedMs = vi.fn((ms: number) =>
      Math.abs(ms - 9000) <= 200 ? 9000 : ms,
    );
    const { props, rightHandle } = setup({ getSnappedMs });
    fireEvent.pointerDown(rightHandle, { pointerId: 6, clientX: 800 });
    fireEvent.pointerMove(rightHandle, { pointerId: 6, clientX: 890 });
    expect(props.setSnapMarkerMs).toHaveBeenLastCalledWith(9000);
    expect(props.setDragPreview).toHaveBeenLastCalledWith({
      clipId: "clip-1",
      startMs: 5000,
      endMs: 9000,
    });
    fireEvent.pointerUp(rightHandle, { pointerId: 6, clientX: 890 });
    expect(props.onTrim).toHaveBeenCalledWith(brollClip, "right", 9000);
    expect(getSnappedMs).toHaveBeenCalledWith(8900, "clip-1");
  });
});

describe("ClipBlock keyboard nudges", () => {
  it("five held ArrowLeft presses preview each step and save once on release", () => {
    const { props, clip } = setup();
    for (let index = 0; index < 5; index += 1) {
      fireEvent.keyDown(clip, { key: "ArrowLeft", repeat: index > 0 });
    }
    expect(props.onMove).not.toHaveBeenCalled();
    expect(props.setDragPreview).toHaveBeenLastCalledWith({
      clipId: "clip-1",
      startMs: 2500,
      endMs: 5500,
    });
    fireEvent.keyUp(clip, { key: "ArrowLeft" });
    expect(props.onMove).toHaveBeenCalledTimes(1);
    expect(props.onMove).toHaveBeenCalledWith(brollClip, 2500);
    expect(props.setDragPreview).toHaveBeenLastCalledWith(null);
  });

  it("separate presses save once after the keyboard goes idle", () => {
    vi.useFakeTimers();
    const { props, clip } = setup();
    fireEvent.keyDown(clip, { key: "ArrowRight" });
    fireEvent.keyDown(clip, { key: "ArrowRight" });
    act(() => {
      vi.advanceTimersByTime(NUDGE_COMMIT_DELAY_MS - 1);
    });
    expect(props.onMove).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(props.onMove).toHaveBeenCalledTimes(1);
    expect(props.onMove).toHaveBeenCalledWith(brollClip, 6000);
  });

  it("saves a pending nudge when focus leaves the clip", () => {
    const { props, clip } = setup();
    fireEvent.keyDown(clip, { key: "ArrowRight" });
    fireEvent.blur(clip);
    expect(props.onMove).toHaveBeenCalledTimes(1);
    expect(props.onMove).toHaveBeenCalledWith(brollClip, 5500);
  });
});
