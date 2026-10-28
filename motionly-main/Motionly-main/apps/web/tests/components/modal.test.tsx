import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Modal } from "@/components/modal";
import { ConflictDialog } from "@/components/editor/ConflictDialog";

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});
afterEach(() => {
  vi.useRealTimers();
});

function settle() {
  act(() => {
    vi.advanceTimersByTime(50);
  });
}

function Harness({
  onOuterClose = () => {},
  onInnerClose = () => {},
}: {
  onOuterClose?: () => void;
  onInnerClose?: () => void;
}) {
  const [outer, setOuter] = useState(false);
  const [inner, setInner] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOuter(true)}>
        Open outer
      </button>
      <Modal
        open={outer}
        title="Outer"
        onClose={() => {
          onOuterClose();
          setOuter(false);
        }}
        footer={
          <>
            <button type="button">First action</button>
            <button type="button" onClick={() => setInner(true)}>
              Open inner
            </button>
          </>
        }
      />
      <Modal
        open={inner}
        title="Inner"
        onClose={() => {
          onInnerClose();
          setInner(false);
        }}
        footer={<button type="button">Inner action</button>}
      />
    </>
  );
}

describe("Modal", () => {
  it("moves focus inside, wraps Tab, and returns focus to the opener", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open outer" });
    await user.click(opener);
    settle();
    const dialog = screen.getByRole("dialog", { name: "Outer" });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);

    // Close modal → First action → Open inner → back to Close modal.
    await user.tab();
    await user.tab();
    await user.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("closes only the topmost dialog on Escape", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const onOuterClose = vi.fn();
    const onInnerClose = vi.fn();
    render(<Harness onOuterClose={onOuterClose} onInnerClose={onInnerClose} />);
    await user.click(screen.getByRole("button", { name: "Open outer" }));
    settle();
    await user.click(screen.getByRole("button", { name: "Open inner" }));
    settle();

    await user.keyboard("{Escape}");
    expect(onInnerClose).toHaveBeenCalledTimes(1);
    expect(onOuterClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Outer" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(onOuterClose).toHaveBeenCalledTimes(1);
  });

  it("ignores the backdrop, Escape and Close while closing is disabled", () => {
    const onClose = vi.fn();
    render(<Modal open title="Busy" onClose={onClose} closeDisabled />);
    settle();
    fireEvent.click(screen.getByTestId("modal-backdrop"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Close modal" })).toBeDisabled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("keeps the backdrop out of the accessibility tree", () => {
    render(<Modal open title="Plain" onClose={() => {}} />);
    expect(screen.getByTestId("modal-backdrop")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(
      screen.queryByRole("button", { name: /overlay/i }),
    ).not.toBeInTheDocument();
  });
});

describe("ConflictDialog", () => {
  const conflict = {
    expectedRevision: 3,
    currentRevision: 5,
    conflictedAt: Date.now(),
    source: "timeline",
    localSummary: [],
  };

  it("explains a failed reload and stays open", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const onDismiss = vi.fn();
    render(
      <ConflictDialog
        conflict={conflict}
        onReload={vi.fn(async () => {
          throw new Error("Could not reload the plan.");
        })}
        onForceSave={vi.fn(async () => {})}
        onDismiss={onDismiss}
      />,
    );
    await user.click(
      screen.getByRole("button", {
        name: "Reload plan from server and discard local changes",
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Could not reload the plan/,
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("cannot be dismissed while Force Save is running", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const onDismiss = vi.fn();
    let finish: () => void = () => {};
    render(
      <ConflictDialog
        conflict={conflict}
        onReload={vi.fn(async () => {})}
        onForceSave={() =>
          new Promise<void>((resolve) => {
            finish = resolve;
          })
        }
        onDismiss={onDismiss}
      />,
    );
    await user.click(
      screen.getByRole("button", {
        name: "Force save your changes, overwriting the newer version",
      }),
    );
    fireEvent.click(screen.getByTestId("modal-backdrop"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onDismiss).not.toHaveBeenCalled();
    await act(async () => finish());
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
