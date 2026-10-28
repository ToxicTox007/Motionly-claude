import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void) {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const media = window.matchMedia(QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    Boolean(window.matchMedia) &&
    window.matchMedia(QUERY).matches
  );
}

/**
 * Whether the viewer prefers reduced motion, safe to render with. The server
 * cannot know the preference, so this is false on the server and while the
 * page hydrates (motion's useReducedMotion reads it during the first client
 * render, so its markup would not match the server's), the real preference
 * right after, and it follows changes. Animations themselves already respect
 * the preference through MotionProvider's reducedMotion="user".
 */
export function usePrefersReducedMotion() {
  return useSyncExternalStore(subscribe, prefersReducedMotion, () => false);
}
