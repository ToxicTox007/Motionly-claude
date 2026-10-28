import type {
  ComponentPropsWithoutRef,
  HTMLAttributes,
  Ref,
  ReactNode,
  SelectHTMLAttributes,
} from "react";
import { useId } from "react";
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
  const titleId = useId();
  const descriptionId = useId();
  if (!open) return null;
  return (
    <>
      <button
        type="button"
        aria-label="Close modal overlay"
        className="fixed inset-0 z-40 cursor-default bg-background/60 text-left backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        ref={contentRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className="fixed left-1/2 top-1/2 z-50 w-[min(34rem,calc(100vw-1.5rem))] -translate-x-1/2 -translate-y-1/2 rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg"
      >
        <div className="flex items-start justify-between gap-4 pb-3">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-bold text-foreground">
              {title}
            </h2>
            {description ? (
              <p
                id={descriptionId}
                className="mt-1 text-sm font-medium text-muted-foreground"
              >
                {description}
              </p>
            ) : null}
          </div>
          <IconButton
            label="Close modal"
            variant="ghost"
            size="icon"
            onClick={onClose}
          >
            <PixelIcon name="x" className="size-4" />
          </IconButton>
        </div>
        {children ? <div className="py-4">{children}</div> : null}
        {footer ? (
          <div className="mt-2 flex flex-wrap justify-end gap-3 pt-4">
            {footer}
          </div>
        ) : null}
      </div>
    </>
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
      <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 hidden -translate-x-1/2 whitespace-nowrap rounded-neu-sm bg-surface px-2.5 py-1 text-[11px] font-semibold text-foreground shadow-neu-raised-sm group-hover:block group-focus-within:block">
        {label}
      </span>
    </span>
  );
}

export { IconButton };
