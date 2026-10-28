import React from "react";
import { cn } from "@/lib/utils";

export function Label({
  className,
  children,
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn(
        "text-xs font-semibold text-foreground mb-1.5 block",
        className,
      )}
      {...props}
    >
      {children}
    </label>
  );
}

export type PixelInputProps = React.InputHTMLAttributes<HTMLInputElement>;

export function PixelInput({ className, ...props }: PixelInputProps) {
  return (
    <input
      className={cn(
        "flex min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 text-base font-medium shadow-neu-inset-sm transition disabled:cursor-not-allowed disabled:opacity-60 focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus sm:text-sm text-foreground focus:ring-offset-2 focus:ring-offset-background",
        className,
      )}
      {...props}
    />
  );
}

export const Input = PixelInput;

export type PixelTextareaProps =
  React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export function PixelTextarea({ className, ...props }: PixelTextareaProps) {
  return (
    <textarea
      className={cn(
        "flex min-h-[7rem] w-full rounded-neu border border-control bg-surface px-4 py-3 text-base font-medium shadow-neu-inset-sm transition disabled:cursor-not-allowed disabled:opacity-60 focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus sm:text-sm text-foreground focus:ring-offset-2 focus:ring-offset-background",
        className,
      )}
      {...props}
    />
  );
}

export const TextArea = PixelTextarea;

export interface PixelSelectProps extends Omit<
  React.SelectHTMLAttributes<HTMLSelectElement>,
  "onChange"
> {
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
  label?: string;
}

export function PixelSelect({
  className,
  options,
  value,
  onChange,
  label,
  id,
  ...props
}: PixelSelectProps) {
  const selectId =
    id ??
    `select-${label?.toLowerCase().replace(/[^a-z0-9]+/g, "-") ?? "field"}`;
  return (
    <div className="relative w-full">
      <select
        id={selectId}
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "flex min-h-11 w-full appearance-none rounded-neu border border-control bg-surface px-4 py-2 pr-10 text-base font-medium shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus sm:text-sm text-foreground focus:ring-offset-2 focus:ring-offset-background",
          className,
        )}
        {...props}
      >
        {options.map((opt) => (
          <option
            key={opt.value}
            value={opt.value}
            className="bg-surface text-foreground"
          >
            {opt.label}
          </option>
        ))}
      </select>
      <div className="pointer-events-none absolute inset-y-0 right-4 flex items-center">
        <svg
          className="h-4 w-4 text-muted-foreground"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </div>
    </div>
  );
}

export const Select = PixelSelect;

export type LoadingPixelSkeletonProps = React.HTMLAttributes<HTMLDivElement>;

export function LoadingPixelSkeleton({
  className,
  ...props
}: LoadingPixelSkeletonProps) {
  return (
    <div
      className={cn("neu-shimmer h-6 w-full rounded-neu", className)}
      {...props}
    />
  );
}

export interface PanelSectionProps extends React.DetailsHTMLAttributes<HTMLDetailsElement> {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export function PanelSection({
  title,
  defaultOpen,
  children,
  className,
  ...props
}: PanelSectionProps) {
  return (
    <details
      className={cn(
        "group rounded-neu-md bg-surface shadow-neu-raised overflow-hidden",
        className,
      )}
      open={defaultOpen}
      {...props}
    >
      <summary className="flex items-center justify-between rounded-neu-md px-4 py-3 font-semibold text-sm cursor-pointer select-none hover:bg-surface-elevated list-none [&::-webkit-details-marker]:hidden text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus transition-colors">
        <span>{title}</span>
        <svg
          className="size-4 text-muted-foreground transition-transform duration-[140ms] group-open:rotate-90"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
      </summary>
      <div className="p-4 bg-surface">{children}</div>
    </details>
  );
}

export function Slider({
  label,
  value,
  min = 0,
  max = 100,
  onChange,
  step = 1,
  suffix = "%",
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  onChange: (val: number) => void;
  step?: number;
  suffix?: string;
}) {
  const id = `slider-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  const clamp = (next: number) => Math.max(min, Math.min(max, next));
  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    const fastStep = Math.max(step * 10, Math.round((max - min) / 10));
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      event.preventDefault();
      onChange(clamp(value - step));
    } else if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      event.preventDefault();
      onChange(clamp(value + step));
    } else if (event.key === "PageDown") {
      event.preventDefault();
      onChange(clamp(value - fastStep));
    } else if (event.key === "PageUp") {
      event.preventDefault();
      onChange(clamp(value + fastStep));
    } else if (event.key === "Home") {
      event.preventDefault();
      onChange(min);
    } else if (event.key === "End") {
      event.preventDefault();
      onChange(max);
    }
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Label htmlFor={id} className="mb-0">
          {label}
        </Label>
        <span className="rounded-full px-2.5 py-0.5 text-xs font-semibold tabular-nums bg-surface text-foreground shadow-neu-raised-sm">
          {value}
          {suffix}
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={label}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-valuetext={`${value} ${suffix}`}
        onChange={(e) => onChange(parseInt(e.target.value))}
        onKeyDown={onKeyDown}
        style={
          {
            "--range-pct": `${((value - min) / (max - min || 1)) * 100}%`,
          } as React.CSSProperties
        }
        className="pixel-range h-3 w-full rounded-full focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2"
      />
    </div>
  );
}
