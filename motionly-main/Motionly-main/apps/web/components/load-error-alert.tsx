"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { RotateCcw } from "lucide-react";
import { Alert } from "@/components/brutal-ui";
import { Button } from "@/components/button";

/**
 * A data load failed: say so and offer Retry, instead of showing the failure
 * as an empty list. Retry re-renders the page's server data in place.
 */
export function LoadErrorAlert({
  title,
  body = "Motionly could not reach its servers. Nothing was lost; try again in a moment.",
  className,
}: {
  title: string;
  body?: string;
  className?: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <Alert tone="danger" title={title} className={className}>
      <p>{body}</p>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="mt-3"
        disabled={pending}
        aria-busy={pending}
        onClick={() => startTransition(() => router.refresh())}
      >
        <RotateCcw className="size-4" aria-hidden="true" />
        {pending ? "Retrying…" : "Retry"}
      </Button>
    </Alert>
  );
}
