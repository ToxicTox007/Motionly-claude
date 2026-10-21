"use client";

import { LazyMotion, MotionConfig, domAnimation } from "motion/react";
import type { ReactNode } from "react";

// One motion setup for the whole app. LazyMotion with `strict` keeps the
// animation features out of the first bundle and forbids the heavy `motion.*`
// components (use `m.*`). reducedMotion="user" follows the viewer's
// prefers-reduced-motion setting: transforms stop, layout stays put.
export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}
