"use client";

import { LazyMotion, MotionConfig } from "motion/react";
import type { ReactNode } from "react";

// Animation features arrive in their own chunk after the first render;
// until then `m.*` elements render in their final state without animating.
const loadFeatures = () =>
  import("./motion-features").then((mod) => mod.default);

// One motion setup for the whole app. LazyMotion with `strict` keeps the
// animation features out of the first bundle and forbids the heavy `motion.*`
// components (use `m.*`). reducedMotion="user" follows the viewer's
// prefers-reduced-motion setting: transforms stop, layout stays put.
export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}
