"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronLeft, Film, Loader2, XCircle } from "lucide-react";
import type { UploadStatus } from "@motionly/shared";
import { ThemeMenu } from "@/components/theme-menu";
import { UploadPanel } from "@/components/upload-panel";
import { buildApiHeaders, startAdaptivePolling } from "@/lib/client-api";
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
  userId: string;
  apiAccessToken: string;
};

export function UploadProjectScreen(props: ProjectPhaseAuth) {
  return (
    <ProjectPhaseShell title={props.title}>
      <div className="w-full max-w-5xl">
        <div className="mb-8 text-center">
          <div className="pixel-button mx-auto mb-4 flex size-14 items-center justify-center bg-primary/10">
            <Film className="size-7 text-primary" aria-hidden="true" />
          </div>
          <h1 className="text-2xl font-bold">Upload your footage</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Drop in your raw talking-head video. We&apos;ll transcribe it and
            prepare an AI edit proposal for you to review before it reaches the timeline.
          </p>
        </div>
        <UploadPanel
          projectId={props.projectId}
          workspaceId={props.workspaceId}
          userId={props.userId}
          apiAccessToken={props.apiAccessToken}
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
        const response = await fetch(
          `${env.apiBaseUrl}/projects/${props.projectId}/uploads/${uploadId}`,
          {
            cache: "no-store",
            headers: await buildApiHeaders({
              workspaceId: props.workspaceId,
              userId: props.userId,
              accessToken: props.apiAccessToken,
              json: false,
            }),
            signal,
          },
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
  }, [
    props.apiAccessToken,
    props.projectId,
    props.userId,
    props.workspaceId,
    router,
    status,
    uploadId,
  ]);

  const activeIndex = productFlowStageIndex(status);
  const failed = status === "failed";

  return (
    <ProjectPhaseShell title={props.title} centered>
      <div className="flex w-full max-w-lg flex-col items-center gap-6 text-center">
        <div className="pixel-panel flex size-16 items-center justify-center shadow-soft">
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
          <h1 className="text-lg font-black uppercase tracking-wide">
            {failed ? "Video processing failed" : "Processing video"}
          </h1>
          <p className="text-xs text-muted-foreground">
            {pollError ?? uploadStatusMessage(status)}
          </p>
        </div>

        <div className="w-full space-y-4 border border-border bg-surface p-5 text-left shadow-soft">
          <h2 className="border-b border-border pb-2 text-xs font-black uppercase tracking-wider text-primary">
            Processing stages
          </h2>
          <div className="space-y-2.5 font-bold">
            {PRODUCT_FLOW_STAGES.map((stage, index) => {
              const completed = index < activeIndex;
              const active = !failed && index === activeIndex;
              return (
                <div
                  key={stage.key}
                  className={cn(
                    "flex items-start gap-3 border p-2 transition",
                    active
                      ? "border-primary bg-primary/5"
                      : "border-transparent opacity-60",
                  )}
                >
                  <div className="mt-0.5 flex size-5 shrink-0 items-center justify-center border border-border bg-background text-[10px] font-black">
                    {completed ? "✓" : active ? "▶" : index + 1}
                  </div>
                  <div className="min-w-0">
                    <p
                      className={cn(
                        "text-xs font-black uppercase tracking-wide",
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
            className="pixel-progress h-2.5 w-full max-w-xs border border-border bg-background"
            role="progressbar"
            aria-label="Video processing progress"
            aria-valuemin={0}
            aria-valuemax={PRODUCT_FLOW_STAGES.length}
            aria-valuenow={activeIndex + 1}
          >
            <div
              className="pixel-progress-fill h-full bg-primary transition-all duration-300"
              style={{
                width: `${Math.round(((activeIndex + 0.5) / PRODUCT_FLOW_STAGES.length) * 100)}%`,
              }}
            />
          </div>
        ) : (
          <Link
            href="/projects"
            className="pixel-button inline-flex min-h-11 items-center justify-center bg-surface px-4 text-sm font-black uppercase"
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
      <header className="pixel-grid-fine flex min-h-12 flex-wrap items-center gap-3 border-b border-border bg-surface/95 px-4 py-2">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href="/dashboard"
            className="pixel-button flex items-center gap-1 bg-surface px-2 py-1 text-sm text-muted-foreground transition hover:border-primary/30 hover:bg-muted hover:text-foreground"
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
            Dashboard
          </Link>
          <span className="truncate text-sm font-semibold text-foreground">
            {title}
          </span>
        </div>
        <div className="ml-auto">
          <ThemeMenu items={[{ href: "/dashboard", label: "Dashboard" }]} />
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
