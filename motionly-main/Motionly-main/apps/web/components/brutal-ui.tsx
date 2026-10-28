import type {
  ComponentPropsWithoutRef,
  HTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from "react";
import { cn } from "@/lib/utils";
import { IconButton } from "@/components/button";
import { PixelIcon, type PixelIconName } from "@/components/pixel-icons";

type AlertTone = "info" | "success" | "warning" | "danger";

export function Card({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <article
      className={cn(
        "rounded-neu-lg bg-surface shadow-neu-raised transition-shadow duration-[140ms]",
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
        "min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus disabled:cursor-not-allowed disabled:opacity-60 focus:ring-offset-2 focus:ring-offset-background",
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
        "min-h-32 w-full rounded-neu border border-control bg-surface px-4 py-3 text-sm font-medium text-foreground shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus disabled:cursor-not-allowed disabled:opacity-60 focus:ring-offset-2 focus:ring-offset-background",
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
        "min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus disabled:cursor-not-allowed disabled:opacity-60 focus:ring-offset-2 focus:ring-offset-background",
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
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold shadow-neu-raised-sm",
        variant === "neutral" && "bg-surface text-foreground",
        variant === "primary" && "bg-primary text-primary-foreground",
        variant === "accent" && "bg-accent text-accent-foreground",
        variant === "success" && "bg-success text-ink",
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
        "flex gap-3 rounded-neu-md bg-surface p-4 text-sm shadow-neu-raised",
        className,
      )}
    >
      <span
        className={cn(
          "flex size-9 shrink-0 items-center justify-center rounded-neu-sm shadow-neu-inset-sm",
          tone === "info" && "text-info",
          tone === "success" && "text-success",
          tone === "warning" && "text-warning",
          tone === "danger" && "text-danger",
        )}
      >
        <PixelIcon name={icon[tone]} className="size-5" />
      </span>
      <div className="min-w-0">
        {title ? (
          <p className="font-semibold text-foreground">{title}</p>
        ) : null}
        <div
          className={cn("font-medium text-muted-foreground", title && "mt-1")}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

// The dialog lives in its own client module (it traps focus with effects);
// re-exported here so existing imports keep working.
export { Modal } from "@/components/modal";

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
      <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 hidden -translate-x-1/2 whitespace-nowrap rounded-neu-sm bg-surface px-2.5 py-1 text-[11px] font-semibold text-foreground shadow-neu-raised-sm group-hover:block group-focus-within:block">
        {label}
      </span>
    </span>
  );
}

export { IconButton };
