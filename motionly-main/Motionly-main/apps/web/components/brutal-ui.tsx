import type {
  ComponentPropsWithoutRef,
  HTMLAttributes,
  Ref,
  ReactNode,
  SelectHTMLAttributes,
} from "react";
import { cn } from "@/lib/utils";
import { Button, IconButton } from "@/components/button";
import { PixelIcon, type PixelIconName } from "@/components/pixel-icons";

type AlertTone = "info" | "success" | "warning" | "danger";

export function Panel({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded border-2 border-foreground bg-surface shadow-brutal",
        className,
      )}
      {...props}
    />
  );
}

export function Card({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <article
      className={cn(
        "rounded border-2 border-foreground bg-surface shadow-brutal transition-all duration-[100ms]",
        className,
      )}
      {...props}
    />
  );
}

export function Input({
  className,
  ...props
}: ComponentPropsWithoutRef<"input">) {
  return (
    <input
      className={cn(
        "min-h-11 w-full rounded border-2 border-foreground bg-surface-elevated px-3 py-2 text-sm font-bold text-foreground transition focus:bg-surface focus:outline-none focus:shadow-brutal-sm disabled:cursor-not-allowed disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}

export function Textarea({
  className,
  ...props
}: ComponentPropsWithoutRef<"textarea">) {
  return (
    <textarea
      className={cn(
        "min-h-32 w-full rounded border-2 border-foreground bg-surface-elevated px-3 py-2 text-sm font-bold text-foreground transition focus:bg-surface focus:outline-none focus:shadow-brutal-sm disabled:cursor-not-allowed disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}

export function Select({
  className,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "min-h-11 w-full rounded border-2 border-foreground bg-surface-elevated px-3 py-2 text-sm font-bold text-foreground transition focus:bg-surface focus:outline-none focus:shadow-brutal-sm disabled:cursor-not-allowed disabled:opacity-60",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}

export function Badge({
  variant = "neutral",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  variant?: "neutral" | "primary" | "accent" | "success" | "warning" | "danger";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border-2 border-foreground px-2 py-0.5 font-mono text-[10px] font-black uppercase",
        variant === "neutral" && "bg-muted text-foreground",
        variant === "primary" && "bg-primary text-primary-foreground",
        variant === "accent" && "bg-accent text-accent-foreground",
        variant === "success" && "bg-success text-white",
        variant === "warning" && "bg-warning text-ink",
        variant === "danger" && "bg-danger text-danger-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function Alert({
  tone = "info",
  title,
  children,
  className,
}: HTMLAttributes<HTMLDivElement> & {
  tone?: AlertTone;
  title?: ReactNode;
}) {
  const icon: Record<AlertTone, PixelIconName> = {
    info: "info",
    success: "success",
    warning: "warning",
    danger: "warning",
  };

  return (
    <div
      role={tone === "danger" || tone === "warning" ? "alert" : "status"}
      className={cn(
        "flex gap-3 rounded border-2 border-foreground border-l-[10px] bg-surface p-4 shadow-brutal-sm text-sm",
        tone === "info" && "border-l-info",
        tone === "success" && "border-l-success",
        tone === "warning" && "border-l-warning",
        tone === "danger" && "border-l-danger",
        className,
      )}
    >
      <PixelIcon
        name={icon[tone]}
        className={cn(
          "mt-0.5 size-5",
          tone === "info" && "text-info",
          tone === "success" && "text-success",
          tone === "warning" && "text-warning",
          tone === "danger" && "text-danger",
        )}
      />
      <div className="min-w-0">
        {title ? <p className="font-black text-foreground">{title}</p> : null}
        <div className={cn("font-bold text-muted-foreground", title && "mt-1")}>
          {children}
        </div>
      </div>
    </div>
  );
}

export function ToastSurface({
  tone = "info",
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  tone?: "info" | "success" | "warning" | "danger" | "loading";
}) {
  return (
    <div
      className={cn(
        "rounded border-2 border-foreground border-l-[10px] bg-surface p-4 text-foreground shadow-brutal",
        tone === "success" && "border-l-success",
        tone === "danger" && "border-l-danger",
        tone === "warning" && "border-l-warning",
        (tone === "info" || tone === "loading") && "border-l-primary",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function Modal({
  open,
  title,
  description,
  children,
  footer,
  onClose,
  contentRef,
}: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  contentRef?: Ref<HTMLDivElement>;
}) {
  if (!open) return null;
  return (
    <>
      <button
        type="button"
        aria-label="Close modal overlay"
        className="fixed inset-0 z-40 cursor-default bg-background/75 text-left"
        onClick={onClose}
      />
      <div
        ref={contentRef}
        role="dialog"
        aria-modal="true"
        className="fixed left-1/2 top-1/2 z-50 w-[min(34rem,calc(100vw-1.5rem))] -translate-x-1/2 -translate-y-1/2 rounded border-4 border-foreground bg-surface p-5 shadow-brutal-lg"
      >
        <div className="flex items-start justify-between gap-4 border-b-2 border-foreground pb-3">
          <div className="min-w-0">
            <h2 className="text-lg font-black uppercase text-foreground">
              {title}
            </h2>
            {description ? (
              <p className="mt-1 text-sm font-bold text-muted-foreground">
                {description}
              </p>
            ) : null}
          </div>
          <IconButton label="Close modal" variant="ghost" size="icon" onClick={onClose}>
            <PixelIcon name="x" className="size-4" />
          </IconButton>
        </div>
        {children ? <div className="py-4">{children}</div> : null}
        {footer ? (
          <div className="flex flex-wrap justify-end gap-3 border-t-2 border-foreground pt-4">
            {footer}
          </div>
        ) : null}
      </div>
    </>
  );
}

export function Tabs({
  tabs,
  value,
  onChange,
  className,
}: {
  tabs: Array<{ value: string; label: string }>;
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "inline-flex rounded border-2 border-foreground bg-surface p-1 shadow-brutal-sm",
        className,
      )}
      role="tablist"
    >
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={value === tab.value}
          className={cn(
            "min-h-10 rounded px-3 text-xs font-black uppercase",
            value === tab.value
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:bg-surface-elevated hover:text-foreground",
          )}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function SegmentedControl({
  options,
  value,
  onChange,
  className,
}: {
  options: Array<{ value: string; label: string }>;
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return <Tabs tabs={options} value={value} onChange={onChange} className={className} />;
}

export function Progress({
  value,
  className,
}: {
  value: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "h-4 overflow-hidden rounded border-2 border-foreground bg-muted",
        className,
      )}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.max(0, Math.min(100, value))}
    >
      <div
        className="h-full border-r-2 border-foreground bg-primary"
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "animate-pulse rounded border-2 border-foreground bg-muted",
        className,
      )}
    />
  );
}

export function Tooltip({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("group relative inline-flex", className)}>
      {children}
      <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 hidden -translate-x-1/2 whitespace-nowrap rounded border-2 border-foreground bg-surface px-2 py-1 text-[10px] font-black uppercase text-foreground shadow-brutal-sm group-hover:block group-focus-within:block">
        {label}
      </span>
    </span>
  );
}

export { Button, IconButton };
