import React from "react";
import { cn } from "@/lib/utils";

export interface PixelCardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

export function PixelCard({ className, children, ...props }: PixelCardProps) {
  return (
    <div
      className={cn(
        "pixel-panel pixel-corners p-5 transition duration-80ms steps(4, end) hover:-translate-y-0.5 hover:shadow-[12px_12px_0_hsl(var(--shadow-color)/0.08)]",
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
        "mb-2 block text-sm font-bold text-muted-foreground",
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
        "pixel-input flex min-h-11 w-full px-4 py-2 text-base transition-all disabled:cursor-not-allowed disabled:opacity-60 hover:border-primary/40 focus:outline-none sm:text-sm",
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
        "pixel-input flex min-h-28 w-full resize-y px-4 py-3 text-base transition-all disabled:cursor-not-allowed disabled:opacity-60 hover:border-primary/40 focus:outline-none sm:text-sm",
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
          "pixel-input flex min-h-11 w-full appearance-none px-4 py-2 pr-10 text-base transition-all hover:border-primary/40 focus:outline-none sm:text-sm",
          className,
        )}
        {...props}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      <div className="pointer-events-none absolute inset-y-0 right-4 flex items-center">
        <svg
          className="h-4 w-4 text-muted-foreground"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </div>
    </div>
  );
}

export const Select = PixelSelect;

export interface PixelButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
}

export function PixelButton({
  className,
  variant = "primary",
  ...props
}: PixelButtonProps) {
  return (
    <button
      className={cn(
        "pixel-button inline-flex min-h-10 items-center justify-center px-4 py-2 text-sm font-bold transition disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-x-0 disabled:hover:translate-y-0 focus:outline-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2",
        variant === "primary" &&
          "bg-primary text-primary-foreground hover:brightness-105",
        variant === "secondary" &&
          "bg-surface text-foreground hover:border-primary/50 hover:bg-muted",
        variant === "danger" &&
          "bg-danger text-white border-danger-strong hover:bg-danger/90",
        variant === "ghost" &&
          "border-transparent bg-transparent text-muted-foreground shadow-none hover:bg-muted hover:text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export interface PixelBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "primary" | "secondary" | "success" | "danger" | "warning";
}

export function PixelBadge({
  className,
  variant = "secondary",
  ...props
}: PixelBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center border border-border px-2 py-0.5 text-[10px] font-extrabold uppercase tracking-wider select-none",
        variant === "primary" && "border-primary/40 bg-primary/10 text-primary",
        variant === "secondary" &&
          "border-border bg-muted/60 text-muted-foreground",
        variant === "success" && "border-success/40 bg-success/10 text-success",
        variant === "danger" && "border-danger/40 bg-danger/10 text-danger",
        variant === "warning" && "border-warning/40 bg-warning/10 text-warning",
        className,
      )}
      {...props}
    />
  );
}

export interface InspectorFieldProps extends React.HTMLAttributes<HTMLDivElement> {
  label?: string;
  htmlFor?: string;
  helperText?: string;
  children: React.ReactNode;
}

export function InspectorField({
  className,
  label,
  htmlFor,
  helperText,
  children,
  ...props
}: InspectorFieldProps) {
  return (
    <div className={cn("space-y-1.5", className)} {...props}>
      {label && (
        <Label htmlFor={htmlFor} className="mb-1">
          {label}
        </Label>
      )}
      {children}
      {helperText && (
        <p className="text-[11px] text-muted-foreground font-medium leading-relaxed">
          {helperText}
        </p>
      )}
    </div>
  );
}

export interface LoadingPixelSkeletonProps extends React.HTMLAttributes<HTMLDivElement> {}

export function LoadingPixelSkeleton({
  className,
  ...props
}: LoadingPixelSkeletonProps) {
  return (
    <div
      className={cn(
        "pixel-skeleton h-4 w-full bg-muted border border-border/30",
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
        "group border border-border bg-surface/55 transition-all duration-75",
        className,
      )}
      open={defaultOpen}
      {...props}
    >
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between px-3 text-sm font-black text-foreground hover:bg-muted/40 transition-colors focus:outline-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-0 select-none [&::-webkit-details-marker]:hidden">
        <span>{title}</span>
        <svg
          className="size-4 text-muted-foreground transition-transform duration-75 group-open:rotate-90"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
      </summary>
      <div className="border-t border-border p-3">{children}</div>
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
        <span className="border border-border bg-surface px-2 py-1 text-xs font-bold text-foreground">
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
        className="pixel-range min-h-11 w-full focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2"
      />
    </div>
  );
}
