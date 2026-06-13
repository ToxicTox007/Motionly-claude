import React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cn("pixel-panel pixel-corners p-5", className)}>
      {children}
    </div>
  );
}

export function Label({ className, children, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn("mb-2 block text-sm font-bold text-muted-foreground", className)} {...props}>
      {children}
    </label>
  );
}

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "pixel-input flex min-h-11 w-full px-4 py-2 text-base transition-all disabled:cursor-not-allowed disabled:opacity-60 hover:border-primary/40 sm:text-sm",
        className
      )}
      {...props}
    />
  );
}

export function TextArea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "pixel-input flex min-h-28 w-full resize-y px-4 py-3 text-base transition-all disabled:cursor-not-allowed disabled:opacity-60 hover:border-primary/40 sm:text-sm",
        className
      )}
      {...props}
    />
  );
}

export function Select({ 
  className, 
  options, 
  value, 
  onChange,
  label,
  id,
}: { 
  className?: string; 
  options: { value: string; label: string }[]; 
  value: string; 
  onChange: (value: string) => void;
  label?: string;
  id?: string;
}) {
  const selectId = id ?? `select-${label?.toLowerCase().replace(/[^a-z0-9]+/g, "-") ?? "field"}`;
  return (
    <div className={cn("relative", className)}>
      <select
        id={selectId}
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="pixel-input flex min-h-11 w-full appearance-none px-4 py-2 pr-10 text-base transition-all hover:border-primary/40 sm:text-sm"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      <div className="pointer-events-none absolute inset-y-0 right-4 flex items-center">
        <svg className="h-4 w-4 text-muted-foreground" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
        </svg>
      </div>
    </div>
  );
}

export function Slider({ 
  label, 
  value, 
  min = 0, 
  max = 100, 
  onChange,
  step = 1,
}: { 
  label: string; 
  value: number; 
  min?: number; 
  max?: number; 
  onChange: (val: number) => void;
  step?: number;
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
        <Label htmlFor={id} className="mb-0">{label}</Label>
        <span className="text-sm font-bold text-foreground">{value}%</span>
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
        aria-valuetext={`${value} percent`}
        onChange={(e) => onChange(parseInt(e.target.value))}
        onKeyDown={onKeyDown}
        className="min-h-11 w-full cursor-pointer accent-primary transition-all hover:brightness-110"
      />
    </div>
  );
}
