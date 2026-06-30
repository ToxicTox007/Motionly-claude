import { AppShell } from "@/components/app-shell";
import { LoadingPixelSkeleton } from "@/components/editor/ui";

export default function BillingLoading() {
  return (
    <AppShell title="Billing">
      <div className="flex flex-col gap-6 animate-pulse">
        {/* Banner Skeleton */}
        <div className="pixel-panel pixel-corners p-5 border-amber-200/50 bg-amber-500/5 flex gap-4">
          <LoadingPixelSkeleton className="size-6 shrink-0" />
          <div className="space-y-2 flex-1">
            <LoadingPixelSkeleton className="h-5 w-48" />
            <LoadingPixelSkeleton className="h-4 w-full" />
          </div>
        </div>

        {/* Current Plan & Usage Stats */}
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="pixel-panel pixel-corners p-5 flex flex-col justify-between min-h-[200px]">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="space-y-2">
                <LoadingPixelSkeleton className="h-4 w-28" />
                <LoadingPixelSkeleton className="h-8 w-48" />
                <LoadingPixelSkeleton className="h-4 w-36" />
              </div>
              <LoadingPixelSkeleton className="h-10 w-36" />
            </div>
            <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 pt-4 border-t border-border">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="border border-border bg-surface/30 p-3 space-y-2">
                  <LoadingPixelSkeleton className="h-3 w-16" />
                  <LoadingPixelSkeleton className="h-5 w-24" />
                </div>
              ))}
            </div>
          </div>

          <div className="pixel-panel pixel-corners p-5 flex flex-col justify-between">
            <div className="space-y-2">
              <LoadingPixelSkeleton className="h-4 w-32" />
              <LoadingPixelSkeleton className="h-8 w-16" />
            </div>
            <LoadingPixelSkeleton className="h-3 w-full mt-4" />
            <LoadingPixelSkeleton className="h-4 w-40 mt-4" />
          </div>
        </div>

        {/* Plans Grid */}
        <div className="grid gap-4 lg:grid-cols-4 pt-4">
          {[1, 2, 3, 4].map((idx) => (
            <div key={idx} className="pixel-panel pixel-corners p-5 space-y-5">
              <div className="space-y-2">
                <LoadingPixelSkeleton className="h-5 w-24" />
                <LoadingPixelSkeleton className="h-8 w-20" />
                <LoadingPixelSkeleton className="h-4 w-16" />
              </div>
              <div className="space-y-2.5 pt-4 border-t border-border">
                <LoadingPixelSkeleton className="h-4 w-full" />
                <LoadingPixelSkeleton className="h-4 w-full" />
                <LoadingPixelSkeleton className="h-4 w-5/6" />
              </div>
              <LoadingPixelSkeleton className="h-10 w-full mt-6" />
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
