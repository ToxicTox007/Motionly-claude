"use client";

import { m } from "motion/react";
import { useState } from "react";
import type { ReactNode } from "react";

// Per-navigation page transition. Next.js remounts this template on every route
// change, so the entrance plays each time. It animates transform only (never a
// fade, which would break mid-animation contrast checks) and settles at
// `transform: none` so position:fixed descendants (editor drawers, modals) are
// not trapped in a transformed containing block. We clear the inline transform
// explicitly on completion as a belt-and-braces guarantee.
export default function Template({ children }: { children: ReactNode }) {
  const [done, setDone] = useState(false);

  return (
    <m.div
      data-page-transition
      initial={{ y: 10, scale: 0.995 }}
      animate={{ y: 0, scale: 1 }}
      transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
      onAnimationComplete={() => setDone(true)}
      style={done ? { transform: "none" } : undefined}
    >
      {children}
    </m.div>
  );
}
