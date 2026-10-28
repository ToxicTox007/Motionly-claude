import { act, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MotionProvider } from "@/components/motion-provider";
import {
  MAX_VISIBLE_TOASTS,
  TOAST_DURATION_MS,
  ToastProvider,
  useToast,
  type Toast,
} from "@/components/toast";

const apiRef: { current: ReturnType<typeof useToast> | null } = {
  current: null,
};
function Capture() {
  const value = useToast();
  useEffect(() => {
    apiRef.current = value;
  });
  return null;
}
const api = {
  toast: (options: Omit<Toast, "id">) => apiRef.current!.toast(options),
  get toasts() {
    return apiRef.current!.toasts;
  },
};

function renderToasts() {
  render(
    <MotionProvider>
      <ToastProvider>
        <Capture />
      </ToastProvider>
    </MotionProvider>,
  );
}

function show(options: Omit<Toast, "id">) {
  let id = "";
  act(() => {
    id = api.toast(options);
  });
  return id;
}

// Dismissed toasts leave state at once; their exit animation then removes
// the node (it needs animation frames, which fake timers do not run).
function titles() {
  return api.toasts.map((t) => t.title);
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("toasts", () => {
  it("keeps error and warning toasts until they are dismissed", () => {
    renderToasts();
    show({ title: "Upload failed", variant: "error" });
    show({ title: "Low audio", variant: "warning", duration: 1000 });
    advance(60_000);
    expect(screen.getByText("Upload failed")).toBeInTheDocument();
    expect(screen.getByText("Low audio")).toBeInTheDocument();
    act(() => {
      fireEvent.click(
        screen.getByRole("button", { name: "Dismiss: Upload failed" }),
      );
    });
    advance(500);
    expect(titles()).not.toContain("Upload failed");
  });

  it("closes success toasts after the default duration", () => {
    renderToasts();
    show({ title: "Saved", variant: "success" });
    advance(TOAST_DURATION_MS - 100);
    expect(screen.getByText("Saved")).toBeInTheDocument();
    advance(1000);
    expect(titles()).not.toContain("Saved");
  });

  it("pauses while hovered and resumes with the time that was left", () => {
    renderToasts();
    show({ title: "Saved", variant: "success" });
    advance(4000);
    const toast = screen.getByText("Saved").closest("[data-variant]")!;
    fireEvent.mouseEnter(toast);
    advance(30_000);
    expect(screen.getByText("Saved")).toBeInTheDocument();
    fireEvent.mouseLeave(toast);
    advance(1500);
    expect(screen.getByText("Saved")).toBeInTheDocument();
    advance(1000);
    expect(titles()).not.toContain("Saved");
  });

  it("pauses while focus is inside", () => {
    renderToasts();
    show({ title: "Copied", variant: "info" });
    act(() => {
      screen.getByRole("button", { name: "Dismiss: Copied" }).focus();
    });
    advance(30_000);
    expect(screen.getByText("Copied")).toBeInTheDocument();
  });

  it("does not restart other toasts' timers when a toast is added", () => {
    renderToasts();
    show({ title: "First", variant: "success" });
    advance(4000);
    show({ title: "Second", variant: "success" });
    advance(2500);
    expect(titles()).not.toContain("First");
    expect(screen.getByText("Second")).toBeInTheDocument();
  });

  it("shows at most three and queues the rest", () => {
    renderToasts();
    for (const n of [1, 2, 3, 4, 5]) {
      show({ title: `Notice ${n}`, variant: "error" });
    }
    expect(screen.getAllByText(/^Notice \d$/)).toHaveLength(MAX_VISIBLE_TOASTS);
    expect(screen.queryByText("Notice 4")).not.toBeInTheDocument();
    act(() => {
      fireEvent.click(
        screen.getByRole("button", { name: "Dismiss: Notice 1" }),
      );
    });
    advance(500);
    expect(screen.getByText("Notice 4")).toBeInTheDocument();
  });

  it("announces errors assertively and the rest politely, without nesting", () => {
    renderToasts();
    show({ title: "Render failed", variant: "error" });
    show({ title: "Saved", variant: "success" });
    const error = screen.getByText("Render failed");
    const saved = screen.getByText("Saved");
    expect(error.closest("[aria-live]")).toHaveAttribute(
      "aria-live",
      "assertive",
    );
    expect(saved.closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
    for (const region of document.querySelectorAll("[aria-live]")) {
      expect(region.parentElement?.closest("[aria-live]")).toBeNull();
    }
    // Titles are paragraphs, not headings.
    expect(screen.queryByRole("heading", { name: "Saved" })).toBeNull();
  });
});
