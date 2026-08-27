import React from "react";
import { cn } from "@/lib/utils";

export interface PixelCardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

export function PixelCard({ className, children, ...props }: PixelCardProps) {
  return (
    <div
      className={cn(
        "rounded-lg bg-surface border-2 border-foreground shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] transition-all p-5",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export const Card = PixelCard;

export function Label({
  className,
  children,
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn(
        "text-xs font-black text-foreground uppercase tracking-wider mb-1.5 block",
        className,
      )}
      {...props}
    >
      {children}
    </label>
  );
}

export interface PixelInputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

export function PixelInput({ className, ...props }: PixelInputProps) {
  return (
    <input
      className={cn(
        "flex min-h-11 w-full rounded-md border-2 border-foreground bg-surface-elevated px-4 py-2 text-base font-bold transition-all disabled:cursor-not-allowed disabled:opacity-60 hover:border-primary focus:bg-surface focus:shadow-[3px_3px_0px_#000] dark:focus:shadow-[3px_3px_0px_#fff] focus:outline-none sm:text-sm text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export const Input = PixelInput;

export interface PixelTextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {}

export function PixelTextarea({ className, ...props }: PixelTextareaProps) {
  return (
    <textarea
      className={cn(
        "flex min-h-[7rem] w-full rounded-md border-2 border-foreground bg-surface-elevated px-4 py-3 text-base font-bold transition-all disabled:cursor-not-allowed disabled:opacity-60 hover:border-primary focus:bg-surface focus:shadow-[3px_3px_0px_#000] dark:focus:shadow-[3px_3px_0px_#fff] focus:outline-none sm:text-sm text-foreground",
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
          "flex min-h-11 w-full appearance-none rounded-md border-2 border-foreground bg-surface-elevated px-4 py-2 pr-10 text-base font-bold transition-all hover:border-primary focus:bg-surface focus:shadow-[3px_3px_0px_#000] dark:focus:shadow-[3px_3px_0px_#fff] focus:outline-none sm:text-sm text-foreground",
          className,
        )}
        {...props}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value} className="bg-surface text-foreground">
            {opt.label}
          </option>
        ))}
      </select>
      <div className="pointer-events-none absolute inset-y-0 right-4 flex items-center">
        <svg
          className="h-4 w-4 text-foreground"
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


export interface LoadingPixelSkeletonProps extends React.HTMLAttributes<HTMLDivElement> {}

export function LoadingPixelSkeleton({
  className,
  ...props
}: LoadingPixelSkeletonProps) {
  return (
    <div
      className={cn(
        "skeleton h-6 border-2 border-foreground rounded-md w-full",
        className,
      )}
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
        "group rounded-lg border-2 border-foreground bg-surface shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] overflow-hidden",
        className,
      )}
      open={defaultOpen}
      {...props}
    >
      <summary className="flex items-center justify-between px-4 py-3 font-bold text-sm cursor-pointer select-none hover:bg-surface-elevated list-none [&::-webkit-details-marker]:hidden text-foreground focus:outline-none border-b-2 border-transparent group-open:border-foreground transition-colors">
        <span>{title}</span>
        <svg
          className="size-4 text-foreground transition-transform duration-[120ms] group-open:rotate-90"
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
        <span className="rounded border-2 border-foreground px-2 py-0.5 text-xs font-bold font-mono bg-surface text-foreground shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
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
        className="pixel-range h-2 w-full rounded-full focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2"
      />
    </div>
  );
}
