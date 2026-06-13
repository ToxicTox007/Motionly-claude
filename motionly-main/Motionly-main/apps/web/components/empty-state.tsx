import type { LucideIcon } from "lucide-react";
import { ButtonLink } from "@/components/button";

export function EmptyState({
  icon: Icon,
  title,
  body,
  actionHref,
  actionLabel,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
  actionHref?: string;
  actionLabel?: string;
}) {
  return (
    <section className="pixel-panel pixel-corners pixel-grid-fine glass-surface overflow-hidden px-6 py-14 text-center">
      <div className="relative mx-auto flex size-14 items-center justify-center border border-border bg-surface text-foreground shadow-soft">
        <span className="absolute -right-2 -top-2 size-2 bg-primary shadow-[8px_8px_0_hsl(var(--primary)/0.28)]" />
        <Icon className="size-6" aria-hidden="true" />
      </div>
      <h2 className="mt-5 text-lg font-black">{title}</h2>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">{body}</p>
      {actionHref && actionLabel ? (
        <ButtonLink href={actionHref} className="mt-6">
          {actionLabel}
        </ButtonLink>
      ) : null}
    </section>
  );
}
