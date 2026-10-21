"use client";

// Decorative neumorphic product mockup for the landing hero. Entirely
// non-interactive: the root is aria-hidden and contains no focusable elements
// (no links, buttons, anchors, focus targets or form fields). Because nothing
// inside is interactive, decorative loops may run continuously — but they stop
// under prefers-reduced-motion, and pointer tilt is disabled for reduced motion
// or coarse pointers. Text only appears light on the dark "ink" video area.
import {
  m,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "motion/react";
import { useEffect, useState, type PointerEvent } from "react";
import { PixelIcon, type PixelIconName } from "@/components/pixel-icons";

const CAPTION_WORDS = ["Your", "story", "cut", "to", "the", "beat"];

const TOOL_RAIL: Array<{ icon: PixelIconName; active?: boolean }> = [
  { icon: "ai-cut", active: true },
  { icon: "captions" },
  { icon: "broll" },
  { icon: "music" },
  { icon: "export" },
];

const LANES: Array<{
  icon: PixelIconName;
  tone: string;
  left: number;
  width: number;
}> = [
  { icon: "ai-cut", tone: "bg-primary", left: 6, width: 42 },
  { icon: "captions", tone: "bg-accent", left: 30, width: 34 },
  { icon: "broll", tone: "bg-info", left: 14, width: 28 },
];

export function HeroConsole() {
  const reduceMotion = useReducedMotion();
  const [coarse, setCoarse] = useState(false);
  const [activeWord, setActiveWord] = useState(0);
  const [ringPct, setRingPct] = useState(0);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(pointer: coarse)");
    const update = () => setCoarse(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  // Caption word sweep + progress ring count-up. Only runs with motion on.
  useEffect(() => {
    if (reduceMotion) return;
    const words = window.setInterval(
      () => setActiveWord((index) => (index + 1) % CAPTION_WORDS.length),
      700,
    );
    const ring = window.setInterval(
      () => setRingPct((pct) => (pct >= 100 ? 0 : pct + 4)),
      120,
    );
    return () => {
      window.clearInterval(words);
      window.clearInterval(ring);
    };
  }, [reduceMotion]);

  const tiltEnabled = !reduceMotion && !coarse;

  // Pointer parallax tilt (≤ 6°). Values stay at 0 when disabled.
  const pointerX = useMotionValue(0.5);
  const pointerY = useMotionValue(0.5);
  const rotateX = useSpring(useTransform(pointerY, [0, 1], [6, -6]), {
    stiffness: 120,
    damping: 18,
  });
  const rotateY = useSpring(useTransform(pointerX, [0, 1], [-6, 6]), {
    stiffness: 120,
    damping: 18,
  });

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!tiltEnabled) return;
    const rect = event.currentTarget.getBoundingClientRect();
    pointerX.set((event.clientX - rect.left) / rect.width);
    pointerY.set((event.clientY - rect.top) / rect.height);
  };

  const resetTilt = () => {
    pointerX.set(0.5);
    pointerY.set(0.5);
  };

  return (
    <div
      aria-hidden="true"
      className="relative select-none [perspective:1600px]"
      onPointerMove={handlePointerMove}
      onPointerLeave={resetTilt}
    >
      <m.div
        style={
          tiltEnabled
            ? { rotateX, rotateY, transformStyle: "preserve-3d" }
            : undefined
        }
        className="relative rounded-neu-2xl bg-surface p-4 shadow-neu-raised-lg sm:p-5"
      >
        {/* Soft teal ambient glow behind the device. */}
        <div className="pointer-events-none absolute -inset-2 -z-10 rounded-neu-2xl bg-primary/10 blur-2xl" />

        <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-4">
          {/* Tool rail */}
          <div className="flex flex-col items-center gap-2 rounded-neu-xl bg-surface p-2 shadow-neu-inset-sm">
            {TOOL_RAIL.map((tool, index) => (
              <span
                key={index}
                className={
                  tool.active
                    ? "flex size-10 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow"
                    : "flex size-10 items-center justify-center rounded-neu bg-surface text-muted-foreground shadow-neu-raised-sm"
                }
              >
                <PixelIcon name={tool.icon} className="size-5" />
              </span>
            ))}
          </div>

          <div className="flex flex-col gap-4">
            {/* Phone preview with sequential caption highlight */}
            <div className="flex justify-center">
              <div className="relative aspect-[9/16] h-[230px] overflow-hidden rounded-neu-lg bg-ink shadow-neu-inset sm:h-[250px]">
                <div className="absolute inset-0 flex items-center justify-center">
                  <m.span
                    className="flex size-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-neu-glow"
                    animate={reduceMotion ? undefined : { scale: [1, 1.08, 1] }}
                    transition={{
                      duration: 2.4,
                      repeat: Infinity,
                      ease: "easeInOut",
                    }}
                  >
                    <PixelIcon name="play" className="size-7" />
                  </m.span>
                </div>
                {/* Caption words — light text on dark ink only. */}
                <div className="absolute inset-x-3 bottom-4 flex flex-wrap justify-center gap-1.5">
                  {CAPTION_WORDS.map((word, index) => (
                    <span
                      key={word}
                      className={
                        index === activeWord
                          ? "rounded-neu-sm bg-primary px-1.5 py-0.5 text-[13px] font-bold text-primary-foreground"
                          : "rounded-neu-sm px-1.5 py-0.5 text-[13px] font-semibold text-white/80"
                      }
                    >
                      {word}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* Dials, toggles and progress ring */}
            <div className="flex items-center gap-3">
              {/* Knob / dial */}
              <div className="flex size-14 items-center justify-center rounded-full bg-surface shadow-neu-raised">
                <m.span
                  className="relative flex size-9 items-center justify-center rounded-full bg-muted shadow-neu-inset-sm"
                  animate={reduceMotion ? undefined : { rotate: [0, 40, 0] }}
                  transition={{
                    duration: 3,
                    repeat: Infinity,
                    ease: "easeInOut",
                  }}
                >
                  <span className="absolute top-1 h-2 w-0.5 rounded-full bg-primary" />
                </m.span>
              </div>

              {/* Two toggles */}
              <div className="flex flex-1 flex-col gap-2">
                {[true, false].map((on, index) => (
                  <span
                    key={index}
                    className="flex h-7 w-12 items-center rounded-full bg-muted p-1 shadow-neu-inset-sm"
                  >
                    <m.span
                      className={
                        on
                          ? "size-5 rounded-full bg-primary shadow-neu-raised-sm"
                          : "size-5 rounded-full bg-surface shadow-neu-raised-sm"
                      }
                      animate={
                        reduceMotion ? undefined : { x: on ? [16, 16] : [0, 0] }
                      }
                      style={{ marginLeft: on ? 16 : 0 }}
                    />
                  </span>
                ))}
              </div>

              {/* Progress ring counting up */}
              <div className="relative flex size-14 items-center justify-center rounded-full bg-surface shadow-neu-raised">
                <div
                  className="size-11 rounded-full"
                  style={{
                    background: `conic-gradient(hsl(var(--primary)) ${ringPct * 3.6}deg, hsl(var(--muted)) 0deg)`,
                  }}
                />
                <div className="absolute flex size-8 items-center justify-center rounded-full bg-surface text-[11px] font-bold tabular-nums text-primary shadow-neu-inset-sm">
                  {ringPct}
                </div>
              </div>
            </div>

            {/* Mini timeline: clips slide on lanes, teal playhead sweeps */}
            <div className="relative rounded-neu-lg bg-surface p-3 shadow-neu-inset">
              <m.span
                className="absolute inset-y-2 z-10 w-0.5 rounded-full bg-primary shadow-neu-glow"
                animate={
                  reduceMotion ? { left: "40%" } : { left: ["10%", "88%"] }
                }
                transition={{
                  duration: 4,
                  repeat: Infinity,
                  ease: "easeInOut",
                }}
              />
              <div className="flex flex-col gap-2">
                {LANES.map((lane, index) => (
                  <div
                    key={index}
                    className="relative h-6 rounded-full bg-muted shadow-neu-inset-sm"
                  >
                    <m.span
                      className={`absolute top-1 bottom-1 flex items-center justify-center rounded-full ${lane.tone} shadow-neu-raised-sm`}
                      style={{ width: `${lane.width}%` }}
                      animate={
                        reduceMotion
                          ? { left: `${lane.left}%` }
                          : {
                              left: [
                                `${lane.left}%`,
                                `${lane.left + 8}%`,
                                `${lane.left}%`,
                              ],
                            }
                      }
                      transition={{
                        duration: 3.6 + index,
                        repeat: Infinity,
                        ease: "easeInOut",
                      }}
                    >
                      <PixelIcon
                        name={lane.icon}
                        className="size-3 text-primary-foreground"
                      />
                    </m.span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </m.div>
    </div>
  );
}
