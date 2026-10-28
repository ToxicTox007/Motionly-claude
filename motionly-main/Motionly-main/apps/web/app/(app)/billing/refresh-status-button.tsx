"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/button";

/**
 * The payment gateway does not return the viewer to a known URL after paying,
 * and the plan only changes once its signed webhook lands. This re-reads the
 * billing status in place so the owner can confirm the new plan.
 */
export function RefreshStatusButton() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      disabled={pending}
      aria-busy={pending}
      onClick={() => startTransition(() => router.refresh())}
    >
      <RotateCcw className="size-4" aria-hidden="true" />
      {pending ? "Refreshing…" : "Refresh status"}
    </Button>
  );
}
