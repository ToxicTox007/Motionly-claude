import { cn } from "@/lib/utils";

// An inset neumorphic progress ring. The track is a sunken circle; the teal
// fill sweeps clockwise from the top. Exposes the progressbar role and value
// semantics so assistive tech reads it the same as a linear bar.
export function ProgressRing({
  value,
  label,
  size = 56,
  className,
}: {
  value: number;
  /** Accessible name, e.g. "Renders used this period: 3 of 10". */
  label: string;
  size?: number;
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(100, value));
  const stroke = Math.max(4, Math.round(size * 0.12));
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const dash = (clamped / 100) * circumference;

  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      className={cn(
        "relative inline-flex shrink-0 items-center justify-center rounded-full bg-surface shadow-neu-inset-sm",
        className,
      )}
      style={{ width: size, height: size }}
    >
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        aria-hidden="true"
        className="-rotate-90"
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          className="stroke-muted"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circumference}`}
          className="stroke-primary transition-[stroke-dasharray] duration-[600ms] ease-spring"
        />
      </svg>
    </div>
  );
}
