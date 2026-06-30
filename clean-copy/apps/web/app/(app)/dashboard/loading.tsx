import { AppShell } from "@/components/app-shell";
import { LoadingPixelSkeleton } from "@/components/editor/ui";

export default function DashboardLoading() {
  return (
    <AppShell title="Dashboard">
      <div className="flex flex-col gap-8 animate-pulse">
        {/* Header Skeleton */}
        <div className="pixel-panel pixel-corners pixel-grid-fine flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 space-y-2">
            <LoadingPixelSkeleton className="h-4 w-24" />
            <LoadingPixelSkeleton className="h-8 w-48" />
            <LoadingPixelSkeleton className="h-4 w-64" />
          </div>
          <LoadingPixelSkeleton className="h-12 w-40 shrink-0" />
        </div>

        {/* Bento grid skeletons */}
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2 pixel-panel pixel-corners p-6 flex flex-col justify-between min-h-[320px] bg-surface/50">
            <div className="space-y-4">
              <LoadingPixelSkeleton className="size-14" />
              <div className="space-y-2">
                <LoadingPixelSkeleton className="h-6 w-72" />
                <LoadingPixelSkeleton className="h-4 w-96" />
              </div>
            </div>
            <div className="flex justify-between items-center mt-8 pt-4 border-t border-border">
              <LoadingPixelSkeleton className="h-4 w-64" />
              <div className="flex gap-3">
                <LoadingPixelSkeleton className="h-8 w-24" />
                <LoadingPixelSkeleton className="h-8 w-16" />
              </div>
            </div>
          </div>

          <div className="pixel-panel pixel-corners p-6 flex flex-col justify-between bg-surface/50 border-dashed">
            <div className="space-y-4">
              <LoadingPixelSkeleton className="h-4 w-28" />
              <LoadingPixelSkeleton className="h-3 w-36" />
              <div className="space-y-3 mt-6">
                <LoadingPixelSkeleton className="h-6 w-full" />
                <LoadingPixelSkeleton className="h-6 w-full" />
                <LoadingPixelSkeleton className="h-6 w-full" />
              </div>
            </div>
            <LoadingPixelSkeleton className="h-10 w-full mt-6" />
          </div>
        </div>

        {/* Grid search + items skeletons */}
        <div className="space-y-6 border-t border-border pt-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <LoadingPixelSkeleton className="h-11 w-full max-w-md" />
            <LoadingPixelSkeleton className="h-11 w-32" />
          </div>

          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {[1, 2, 3].map((idx) => (
              <div
                key={idx}
                className="pixel-panel pixel-corners p-5 flex flex-col justify-between min-h-[190px]"
              >
                <div className="space-y-3">
                  <div className="flex justify-between items-start gap-4">
                    <LoadingPixelSkeleton className="h-5 w-32" />
                    <LoadingPixelSkeleton className="h-4 w-12" />
                  </div>
                  <LoadingPixelSkeleton className="h-4 w-48" />
                </div>
                <div className="flex justify-between items-center mt-6 pt-3 border-t border-border">
                  <LoadingPixelSkeleton className="h-4 w-28" />
                  <div className="flex gap-2">
                    <LoadingPixelSkeleton className="h-6 w-12" />
                    <LoadingPixelSkeleton className="h-6 w-12" />
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
