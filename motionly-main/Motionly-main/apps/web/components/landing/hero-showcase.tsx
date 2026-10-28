"use client";

import { useState } from "react";
import { useReducedMotion } from "motion/react";
import { Pause, Play } from "lucide-react";
import { HeroConsole } from "./hero-console";

/**
 * The landing hero mockup plus a pause control for its looping animation
 * (WCAG 2.2.2). The mockup stays decorative and aria-hidden; the control sits
 * outside it. Under reduced motion nothing loops, so there is no control.
 */
export function HeroShowcase() {
  const reduceMotion = useReducedMotion();
  const [paused, setPaused] = useState(false);

  return (
    <div className="relative">
      <HeroConsole paused={paused} />
      {reduceMotion ? null : (
        <button
          type="button"
          onClick={() => setPaused((value) => !value)}
          aria-label={paused ? "Play animation" : "Pause animation"}
          className="absolute bottom-3 right-3 z-10 inline-flex size-11 items-center justify-center rounded-full bg-surface text-muted-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:text-foreground hover:shadow-neu-raised active:shadow-neu-pressed"
        >
          {paused ? (
            <Play className="size-4" aria-hidden="true" />
          ) : (
            <Pause className="size-4" aria-hidden="true" />
          )}
        </button>
      )}
    </div>
  );
}
