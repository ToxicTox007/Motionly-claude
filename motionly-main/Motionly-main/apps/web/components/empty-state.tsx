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
    <section className="rounded-lg border border-dashed border-border bg-surface px-6 py-14 text-center shadow-sm">
      <div className="mx-auto flex size-12 items-center justify-center rounded-lg bg-muted text-foreground">
        <Icon className="size-6" aria-hidden="true" />
      </div>
      <h2 className="mt-5 text-lg font-bold">{title}</h2>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">{body}</p>
      {actionHref && actionLabel ? (
        <ButtonLink href={actionHref} className="mt-6">
          {actionLabel}
        </ButtonLink>
      ) : null}
    </section>
  );
}
