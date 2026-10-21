import { AppShell } from "@/components/app-shell";
import { LoadingPixelSkeleton } from "@/components/editor/ui";

export default function BillingLoading() {
  return (
    <AppShell title="Billing">
      <div className="flex flex-col gap-6">
        {/* Banner Skeleton */}
        <div className="flex gap-4 rounded-neu-lg bg-surface p-5 shadow-neu-raised ring-1 ring-warning/30">
          <LoadingPixelSkeleton className="size-6 shrink-0" />
          <div className="flex-1 space-y-2">
            <LoadingPixelSkeleton className="h-5 w-48" />
            <LoadingPixelSkeleton className="h-4 w-full" />
          </div>
        </div>

        {/* Current Plan & Usage Stats */}
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="flex min-h-[200px] flex-col justify-between rounded-neu-lg bg-surface p-5 shadow-neu-raised">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="space-y-2">
                <LoadingPixelSkeleton className="h-4 w-28" />
                <LoadingPixelSkeleton className="h-8 w-48" />
                <LoadingPixelSkeleton className="h-4 w-36" />
              </div>
              <LoadingPixelSkeleton className="h-10 w-36" />
            </div>
            <div className="mt-6 grid gap-3 border-t border-hairline pt-4 sm:grid-cols-2 lg:grid-cols-4">
              {[1, 2, 3, 4].map((i) => (
                <div
                  key={i}
                  className="space-y-2 rounded-neu-md bg-surface p-3 shadow-neu-inset-sm"
                >
                  <LoadingPixelSkeleton className="h-3 w-16" />
                  <LoadingPixelSkeleton className="h-5 w-24" />
                </div>
              ))}
            </div>
          </div>

          <div className="flex flex-col justify-between rounded-neu-lg bg-surface p-5 shadow-neu-raised">
            <div className="space-y-2">
              <LoadingPixelSkeleton className="h-4 w-32" />
              <LoadingPixelSkeleton className="h-8 w-16" />
            </div>
            <LoadingPixelSkeleton className="mt-4 h-3 w-full" />
            <LoadingPixelSkeleton className="mt-4 h-4 w-40" />
          </div>
        </div>

        {/* Plans Grid */}
        <div className="grid gap-4 pt-4 lg:grid-cols-4">
          {[1, 2, 3, 4].map((idx) => (
            <div
              key={idx}
              className="space-y-5 rounded-neu-lg bg-surface p-5 shadow-neu-raised"
            >
              <div className="space-y-2">
                <LoadingPixelSkeleton className="h-5 w-24" />
                <LoadingPixelSkeleton className="h-8 w-20" />
                <LoadingPixelSkeleton className="h-4 w-16" />
              </div>
              <div className="space-y-2.5 border-t border-hairline pt-4">
                <LoadingPixelSkeleton className="h-4 w-full" />
                <LoadingPixelSkeleton className="h-4 w-full" />
                <LoadingPixelSkeleton className="h-4 w-5/6" />
              </div>
              <LoadingPixelSkeleton className="mt-6 h-10 w-full" />
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
