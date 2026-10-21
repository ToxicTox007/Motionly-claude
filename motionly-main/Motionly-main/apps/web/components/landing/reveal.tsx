"use client";

// Scroll-reveal wrapper for the landing page. Moves content up into place the
// first time it enters the viewport. Transform-only (no opacity) so axe can
// measure text contrast at every frame, and it ends at transform: none.
import { m } from "motion/react";
import type { ReactNode } from "react";

export function Reveal({
  children,
  delay = 0,
  className,
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  return (
    <m.div
      className={className}
      initial={{ y: 24 }}
      whileInView={{ y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1], delay }}
    >
      {children}
    </m.div>
  );
}
