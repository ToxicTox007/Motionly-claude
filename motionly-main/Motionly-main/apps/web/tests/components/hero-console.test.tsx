import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MotionProvider } from "@/components/motion-provider";
import { HeroConsole } from "@/components/landing/hero-console";
import { HeroShowcase } from "@/components/landing/hero-showcase";

type ObserverCallback = (entries: Array<{ isIntersecting: boolean }>) => void;
const observers: ObserverCallback[] = [];

class FakeIntersectionObserver {
  constructor(callback: ObserverCallback) {
    observers.push(callback);
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

function setIntersecting(isIntersecting: boolean) {
  act(() => {
    for (const callback of observers) callback([{ isIntersecting }]);
  });
}

type TimerSpy = {
  mock: { calls: unknown[][]; results: Array<{ value: unknown }> };
};
let setIntervalSpy: TimerSpy;
let clearIntervalSpy: TimerSpy;

// The caption loop is the hero's 700 ms interval; other timers (motion's own
// frame scheduling) are not the hero's to manage.
function activeIntervals() {
  const created = setIntervalSpy.mock.calls
    .map((call, index): [unknown, unknown] => [
      call[1],
      setIntervalSpy.mock.results[index]?.value,
    ])
    .filter(([delay]) => delay === 700)
    .map(([, id]) => id);
  const cleared = new Set(clearIntervalSpy.mock.calls.map((call) => call[0]));
  return created.filter((id) => !cleared.has(id)).length;
}

beforeEach(() => {
  observers.length = 0;
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  setIntervalSpy = vi.spyOn(window, "setInterval") as unknown as TimerSpy;
  clearIntervalSpy = vi.spyOn(window, "clearInterval") as unknown as TimerSpy;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HeroConsole", () => {
  it("runs its caption loop only while it is on screen", () => {
    render(
      <MotionProvider>
        <HeroConsole />
      </MotionProvider>,
    );
    setIntersecting(true);
    expect(activeIntervals()).toBe(1);
    setIntersecting(false);
    expect(activeIntervals()).toBe(0);
    setIntersecting(true);
    expect(activeIntervals()).toBe(1);
  });

  it("stops while the tab is hidden", () => {
    render(
      <MotionProvider>
        <HeroConsole />
      </MotionProvider>,
    );
    setIntersecting(true);
    const visibility = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(activeIntervals()).toBe(0);
    visibility.mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(activeIntervals()).toBe(1);
  });

  it("stays still when paused", () => {
    render(
      <MotionProvider>
        <HeroConsole paused />
      </MotionProvider>,
    );
    setIntersecting(true);
    expect(activeIntervals()).toBe(0);
  });
});

describe("HeroShowcase", () => {
  it("offers an accessible pause control outside the decorative mockup", async () => {
    const user = userEvent.setup();
    render(
      <MotionProvider>
        <HeroShowcase />
      </MotionProvider>,
    );
    setIntersecting(true);
    const pause = screen.getByRole("button", { name: "Pause animation" });
    expect(pause.closest('[aria-hidden="true"]')).toBeNull();
    await user.click(pause);
    expect(activeIntervals()).toBe(0);
    await user.click(screen.getByRole("button", { name: "Play animation" }));
    expect(activeIntervals()).toBe(1);
  });
});
