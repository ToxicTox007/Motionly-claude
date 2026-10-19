import type { LucideIcon } from "lucide-react";
import { ButtonLink } from "@/components/button";
import { cn } from "@/lib/utils";

export function EmptyState({
  icon: Icon,
  title,
  body,
  actionHref,
  actionLabel,
  children,
  className,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
  actionHref?: string;
  actionLabel?: string;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "rounded-lg border-4 border-dashed border-foreground p-12 text-center bg-surface shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff]",
        className,
      )}
    >
      {/* Icon slot */}
      <div className="size-16 rounded border-2 border-foreground bg-primary flex items-center justify-center animate-float mx-auto mb-6 shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
        <Icon
          className="size-7 text-primary-foreground"
          aria-hidden="true"
          strokeWidth={2}
        />
      </div>

      {/* Headline */}
      <h2 className="text-xl font-black uppercase tracking-tight mt-0 mb-2 text-foreground">
        {title}
      </h2>

      {/* Description */}
      <p className="text-xs font-mono text-muted-foreground mb-6 mx-auto max-w-sm leading-relaxed">
        {body}
      </p>

      {/* CTA slot */}
      {children ? (
        children
      ) : actionHref && actionLabel ? (
        <ButtonLink href={actionHref}>{actionLabel}</ButtonLink>
      ) : null}
    </section>
  );
}
