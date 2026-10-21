import { AppShell } from "@/components/app-shell";
import { LoadingPixelSkeleton } from "@/components/editor/ui";

export default function DashboardLoading() {
  return (
    <AppShell title="Dashboard">
      <div className="flex flex-col gap-8">
        {/* Header Skeleton */}
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 space-y-2">
            <LoadingPixelSkeleton className="h-4 w-24 rounded-full" />
            <LoadingPixelSkeleton className="h-8 w-48" />
            <LoadingPixelSkeleton className="h-4 w-64" />
          </div>
          <LoadingPixelSkeleton className="h-12 w-40 shrink-0" />
        </div>

        {/* Bento grid skeletons */}
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="flex min-h-[320px] flex-col justify-between rounded-neu-lg bg-surface p-6 shadow-neu-raised lg:col-span-2">
            <div className="space-y-4">
              <LoadingPixelSkeleton className="size-14" />
              <div className="space-y-2">
                <LoadingPixelSkeleton className="h-6 w-72" />
                <LoadingPixelSkeleton className="h-4 w-96" />
              </div>
            </div>
            <div className="mt-8 flex items-center justify-between border-t border-hairline pt-4">
              <LoadingPixelSkeleton className="h-4 w-64" />
              <div className="flex gap-3">
                <LoadingPixelSkeleton className="h-8 w-24" />
                <LoadingPixelSkeleton className="h-8 w-16" />
              </div>
            </div>
          </div>

          <div className="flex flex-col justify-between rounded-neu-lg bg-surface p-6 shadow-neu-raised">
            <div className="space-y-4">
              <LoadingPixelSkeleton className="h-4 w-28" />
              <LoadingPixelSkeleton className="h-3 w-36" />
              <div className="mt-6 space-y-3">
                <LoadingPixelSkeleton className="h-6 w-full" />
                <LoadingPixelSkeleton className="h-6 w-full" />
                <LoadingPixelSkeleton className="h-6 w-full" />
              </div>
            </div>
            <LoadingPixelSkeleton className="mt-6 h-10 w-full" />
          </div>
        </div>

        {/* Grid search + items skeletons */}
        <div className="space-y-6 border-t border-hairline pt-6">
          <div className="flex flex-col gap-4 rounded-neu-xl bg-surface px-3 py-2 shadow-neu-raised sm:flex-row sm:items-center sm:justify-between">
            <LoadingPixelSkeleton className="h-11 w-full max-w-md" />
            <LoadingPixelSkeleton className="h-11 w-32 rounded-full" />
          </div>

          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {[1, 2, 3].map((idx) => (
              <div
                key={idx}
                className="flex min-h-[190px] flex-col justify-between rounded-neu-lg bg-surface p-5 shadow-neu-raised"
              >
                <div className="space-y-3">
                  <div className="flex items-start justify-between gap-4">
                    <LoadingPixelSkeleton className="h-5 w-32" />
                    <LoadingPixelSkeleton className="h-4 w-12 rounded-full" />
                  </div>
                  <LoadingPixelSkeleton className="h-4 w-48" />
                </div>
                <div className="mt-6 flex items-center justify-between border-t border-hairline pt-3">
                  <LoadingPixelSkeleton className="h-4 w-28" />
                  <div className="flex gap-2">
                    <LoadingPixelSkeleton className="h-6 w-12 rounded-full" />
                    <LoadingPixelSkeleton className="h-6 w-12 rounded-full" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
