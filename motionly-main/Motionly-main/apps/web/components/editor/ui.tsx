import React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cn("rounded-2xl border border-border bg-surface p-6 shadow-sm", className)}>
      {children}
    </div>
  );
}

export function Label({ className, children, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn("text-xs font-bold uppercase tracking-widest text-muted-foreground/70 mb-2 block", className)} {...props}>
      {children}
    </label>
  );
}

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "flex h-11 w-full rounded-xl border border-border bg-background px-4 py-2 text-sm ring-offset-background transition-all focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 hover:border-muted-foreground/30",
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
        "flex min-h-24 w-full rounded-xl border border-border bg-background px-4 py-3 text-sm ring-offset-background transition-all focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 hover:border-muted-foreground/30",
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
  onChange 
}: { 
  className?: string; 
  options: { value: string; label: string }[]; 
  value: string; 
  onChange: (value: string) => void 
}) {
  return (
    <div className={cn("relative", className)}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="flex h-11 w-full appearance-none rounded-xl border border-border bg-background px-4 py-2 text-sm ring-offset-background transition-all focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 hover:border-muted-foreground/30"
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
  onChange 
}: { 
  label: string; 
  value: number; 
  min?: number; 
  max?: number; 
  onChange: (val: number) => void 
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Label className="mb-0">{label}</Label>
        <span className="text-xs font-bold text-primary">{value}%</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(parseInt(e.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-muted accent-primary transition-all hover:brightness-110"
      />
    </div>
  );
}
