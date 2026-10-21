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
        "rounded-neu-xl p-12 text-center bg-surface shadow-neu-raised-lg",
        className,
      )}
    >
      {/* Icon slot */}
      <div className="size-16 rounded-neu-md bg-primary flex items-center justify-center animate-float mx-auto mb-6 shadow-neu-glow">
        <Icon
          className="size-7 text-primary-foreground"
          aria-hidden="true"
          strokeWidth={2}
        />
      </div>

      {/* Headline */}
      <h2 className="text-xl font-bold tracking-tight mt-0 mb-2 text-foreground">
        {title}
      </h2>

      {/* Description */}
      <p className="text-sm text-muted-foreground mb-6 mx-auto max-w-sm leading-relaxed">
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
