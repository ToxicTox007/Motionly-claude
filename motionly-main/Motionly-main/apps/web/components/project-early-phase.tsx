"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronLeft, Film, Loader2, XCircle } from "lucide-react";
import type { UploadStatus } from "@motionly/shared";
import { MobileNav } from "@/components/mobile-nav";
import { UploadPanel } from "@/components/upload-panel";
import { apiFetch, startAdaptivePolling } from "@/lib/client-api";
import { env } from "@/lib/env";
import {
  PRODUCT_FLOW_STAGES,
  productFlowStageIndex,
  uploadStatusMessage,
} from "@/lib/product-flow-status";
import { cn } from "@/lib/utils";
import { useRouter } from "next/navigation";

type ProjectPhaseAuth = {
  title: string;
  projectId: string;
  workspaceId: string;
};

export function UploadProjectScreen(props: ProjectPhaseAuth) {
  return (
    <ProjectPhaseShell title={props.title}>
      <div className="w-full max-w-5xl">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-neu bg-surface text-primary shadow-neu-inset-sm">
            <Film className="size-7 text-primary" aria-hidden="true" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            Upload your footage
          </h1>
          <p className="mt-2 text-sm font-medium text-muted-foreground">
            Drop in your raw talking-head video. We&apos;ll transcribe it and
            prepare an AI edit proposal for you to review before it reaches the
            timeline.
          </p>
        </div>
        <UploadPanel
          projectId={props.projectId}
          workspaceId={props.workspaceId}
        />
      </div>
    </ProjectPhaseShell>
  );
}

export function ProcessingProjectScreen({
  uploadId,
  initialStatus,
  ...props
}: ProjectPhaseAuth & {
  uploadId: string;
  initialStatus: UploadStatus;
}) {
  const router = useRouter();
  const [status, setStatus] = useState(initialStatus);
  const [pollError, setPollError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "ready" || status === "failed") return;

    let stopPolling: () => void = () => undefined;
    stopPolling = startAdaptivePolling(
      async (signal) => {
        const response = await apiFetch(
          `${env.apiBaseUrl}/projects/${props.projectId}/uploads/${uploadId}`,
          { cache: "no-store", signal },
          { workspaceId: props.workspaceId },
        );
        const payload = (await response.json().catch(() => null)) as {
          status?: UploadStatus;
          error?: { message?: string };
          error_message?: string | null;
        } | null;
        if (!response.ok || !payload?.status) {
          const message =
            payload?.error?.message ??
            `Upload status could not be refreshed (${response.status}).`;
          setPollError(message);
          throw new Error(message);
        }

        setPollError(null);
        setStatus(payload.status);
        if (payload.status === "ready") {
          stopPolling();
          router.refresh();
        } else if (payload.status === "failed") {
          stopPolling();
          setPollError(
            payload.error_message ??
              "Video processing failed. Return to the project and upload another source.",
          );
        }
      },
      {
        intervalMs: 4000,
        maxIntervalMs: 15000,
        backoffStepMs: 1000,
        immediate: false,
      },
    );
    return stopPolling;
  }, [props.projectId, props.workspaceId, router, status, uploadId]);

  const activeIndex = productFlowStageIndex(status);
  const failed = status === "failed";

  return (
    <ProjectPhaseShell title={props.title} centered>
      <div className="flex w-full max-w-lg flex-col items-center gap-6 text-center">
        <div className="flex size-16 items-center justify-center rounded-neu-md bg-surface shadow-neu-raised">
          {failed ? (
            <XCircle className="size-8 text-danger" aria-hidden="true" />
          ) : (
            <Loader2
              className="size-8 animate-spin text-primary"
              aria-hidden="true"
            />
          )}
        </div>

        <div className="space-y-1" aria-live="polite" aria-atomic="true">
          <h1 className="text-lg font-bold tracking-tight text-foreground">
            {failed ? "Video processing failed" : "Processing video"}
          </h1>
          <p className="text-xs font-medium text-muted-foreground">
            {pollError ?? uploadStatusMessage(status)}
          </p>
        </div>

        <div className="w-full space-y-4 rounded-neu-lg bg-surface p-5 text-left shadow-neu-raised">
          <h2 className="border-b border-hairline pb-2 text-xs font-semibold text-primary">
            Processing stages
          </h2>
          <div className="space-y-2.5">
            {PRODUCT_FLOW_STAGES.map((stage, index) => {
              const completed = index < activeIndex;
              const active = !failed && index === activeIndex;
              return (
                <div
                  key={stage.key}
                  className={cn(
                    "flex items-start gap-3 rounded-neu-md p-2 transition-shadow duration-200",
                    active ? "bg-surface shadow-neu-inset-sm" : "opacity-60",
                  )}
                >
                  <div className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-surface text-[10px] font-bold text-foreground shadow-neu-raised-sm">
                    {completed ? "✓" : active ? "▶" : index + 1}
                  </div>
                  <div className="min-w-0">
                    <p
                      className={cn(
                        "text-xs font-semibold",
                        active ? "text-primary" : "text-foreground",
                      )}
                    >
                      {stage.label}
                    </p>
                    {active ? (
                      <p className="mt-0.5 text-[10px] font-medium leading-normal text-muted-foreground">
                        {stage.description}
                      </p>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {!failed ? (
          <div
            className="h-3 w-full max-w-xs overflow-hidden rounded-full bg-surface shadow-neu-inset-sm"
            role="progressbar"
            aria-label="Video processing progress"
            aria-valuemin={0}
            aria-valuemax={PRODUCT_FLOW_STAGES.length}
            aria-valuenow={activeIndex + 1}
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-300"
              style={{
                width: `${Math.round(((activeIndex + 0.5) / PRODUCT_FLOW_STAGES.length) * 100)}%`,
              }}
            />
          </div>
        ) : (
          <Link
            href="/projects"
            className="inline-flex min-h-11 items-center justify-center rounded-neu bg-surface px-4 text-sm font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed"
          >
            Back to projects
          </Link>
        )}
      </div>
    </ProjectPhaseShell>
  );
}

function ProjectPhaseShell({
  title,
  centered = false,
  children,
}: {
  title: string;
  centered?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden bg-background">
      <header className="flex min-h-12 flex-wrap items-center gap-3 border-b border-hairline bg-surface px-4 py-2 shadow-neu-raised-sm">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-1 rounded-neu bg-surface px-3 py-1.5 text-sm font-semibold text-muted-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:text-foreground hover:shadow-neu-raised active:shadow-neu-pressed"
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
            Dashboard
          </Link>
          <span className="truncate text-sm font-semibold text-foreground">
            {title}
          </span>
        </div>
        <div className="ml-auto">
          <MobileNav items={[{ href: "/dashboard", label: "Dashboard" }]} />
        </div>
      </header>
      <main
        className={cn(
          "flex flex-1 px-4 py-10 sm:px-6",
          centered
            ? "items-center justify-center py-8"
            : "items-start justify-center lg:py-16",
        )}
      >
        {children}
      </main>
    </div>
  );
}
