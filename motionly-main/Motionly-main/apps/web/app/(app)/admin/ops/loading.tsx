import { AppShell } from "@/components/app-shell";
import { LoadingPixelSkeleton } from "@/components/editor/ui";

export default function AdminOpsLoading() {
  return (
    <AppShell title="Launch Ops">
      <div className="flex flex-col gap-6 animate-pulse">
        {/* Metric Cards Skeletons */}
        <div className="grid gap-3 md:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="pixel-panel p-4 space-y-3">
              <div className="flex items-center gap-2">
                <LoadingPixelSkeleton className="size-4 shrink-0" />
                <LoadingPixelSkeleton className="h-3.5 w-24" />
              </div>
              <LoadingPixelSkeleton className="h-7 w-16" />
            </div>
          ))}
        </div>

        {/* Queues Table Skeleton */}
        <div className="space-y-3 mt-4">
          <div className="flex justify-between items-center">
            <LoadingPixelSkeleton className="h-5 w-20" />
            <LoadingPixelSkeleton className="h-9 w-32" />
          </div>
          <div className="pixel-panel overflow-hidden">
            <div className="bg-muted p-3 flex justify-between border-b border-border">
              <LoadingPixelSkeleton className="h-4 w-24" />
              <LoadingPixelSkeleton className="h-4 w-16" />
              <LoadingPixelSkeleton className="h-4 w-16" />
              <LoadingPixelSkeleton className="h-4 w-16" />
            </div>
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="p-4 flex justify-between border-b border-border/40"
              >
                <LoadingPixelSkeleton className="h-4 w-32" />
                <LoadingPixelSkeleton className="h-4 w-12" />
                <LoadingPixelSkeleton className="h-4 w-12" />
                <LoadingPixelSkeleton className="h-4 w-12" />
              </div>
            ))}
          </div>
        </div>

        {/* Queue Jobs Skeleton */}
        <div className="space-y-3 mt-4">
          <LoadingPixelSkeleton className="h-5 w-24" />
          <div className="flex flex-wrap items-center gap-3">
            <LoadingPixelSkeleton className="h-10 w-full max-w-sm" />
            <LoadingPixelSkeleton className="h-10 w-36" />
          </div>
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="pixel-panel p-4 flex justify-between items-center"
            >
              <div className="space-y-2">
                <LoadingPixelSkeleton className="h-4 w-48" />
                <LoadingPixelSkeleton className="h-3 w-64" />
              </div>
              <LoadingPixelSkeleton className="h-9 w-20" />
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
