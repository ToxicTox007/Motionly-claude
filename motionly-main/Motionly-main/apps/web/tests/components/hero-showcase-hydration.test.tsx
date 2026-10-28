import { act } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { hasReducedMotionListener } from "motion/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MotionProvider } from "@/components/motion-provider";
import { HeroShowcase } from "@/components/landing/hero-showcase";

// The landing hero must hydrate for a viewer who prefers reduced motion: the
// server cannot know the preference, so the first client render may not
// depend on it (found by the browser suite: React error #418 on / under
// reduced motion).

class FakeIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

function preferReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        // motion asks "(prefers-reduced-motion)", true when it is "reduce".
        matches:
          reduce &&
          query.includes("prefers-reduced-motion") &&
          !query.includes("no-preference"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
  // motion reads the preference once per page; this is a new "page".
  hasReducedMotionListener.current = false;
}

const showcase = () => (
  <MotionProvider>
    <HeroShowcase />
  </MotionProvider>
);

// hydrateRoot directly (not Testing Library), so tell React act() is used.
(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  hasReducedMotionListener.current = false;
});

describe("HeroShowcase hydration", () => {
  it("hydrates without a mismatch when the viewer prefers reduced motion", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
    // The server knows no preference.
    preferReducedMotion(false);
    const html = renderToString(showcase());

    // The viewer's browser prefers reduced motion.
    preferReducedMotion(true);
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.append(container);
    const recoverable = vi.fn();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    await act(async () => {
      hydrateRoot(container, showcase(), { onRecoverableError: recoverable });
    });

    expect(recoverable).not.toHaveBeenCalled();
    expect(errors).not.toHaveBeenCalled();
    // Nothing loops under reduced motion, so no pause control is offered.
    expect(
      container.querySelector('button[aria-label="Pause animation"]'),
    ).toBeNull();
  });

  it("offers the pause control when motion is allowed", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
    preferReducedMotion(false);
    const html = renderToString(showcase());
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.append(container);
    const recoverable = vi.fn();
    await act(async () => {
      hydrateRoot(container, showcase(), { onRecoverableError: recoverable });
    });

    expect(recoverable).not.toHaveBeenCalled();
    expect(
      container.querySelector('button[aria-label="Pause animation"]'),
    ).not.toBeNull();
  });
});
