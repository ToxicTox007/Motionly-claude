import { LoadingPixelSkeleton } from "@/components/editor/ui";
import { PageTitle } from "@/components/page-title";

export default function ProjectDetailsLoading() {
  return (
    <div className="flex min-h-screen flex-col overflow-hidden bg-background">
      <PageTitle title="Project" />
      <div className="pixel-grid-fine flex min-h-12 items-center gap-3 border-b border-border bg-surface/95 px-4 py-2">
        <LoadingPixelSkeleton className="h-6 w-24" />
        <LoadingPixelSkeleton className="h-5 w-40" />
      </div>
      <div className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[220px_minmax(0,1fr)_360px] lg:grid-rows-[minmax(0,1fr)_190px]">
        <div className="pixel-panel order-2 min-h-[260px] space-y-3 p-3 lg:order-1 lg:min-h-0">
          <LoadingPixelSkeleton className="h-10 w-full" />
          <LoadingPixelSkeleton className="h-10 w-full" />
          <LoadingPixelSkeleton className="h-10 w-full" />
        </div>
        <div className="pixel-panel-strong order-1 min-h-[460px] space-y-4 p-4 lg:order-2 lg:min-h-0">
          <LoadingPixelSkeleton className="h-full min-h-[320px] w-full" />
        </div>
        <div className="pixel-panel order-3 min-h-[420px] space-y-4 p-4 lg:order-3 lg:min-h-0">
          <LoadingPixelSkeleton className="h-8 w-32" />
          <LoadingPixelSkeleton className="h-12 w-full" />
          <LoadingPixelSkeleton className="h-12 w-full" />
          <LoadingPixelSkeleton className="h-12 w-full" />
        </div>
        <div className="pixel-panel order-4 min-h-[190px] space-y-3 p-3 lg:col-span-3 lg:min-h-0">
          <LoadingPixelSkeleton className="h-8 w-48" />
          <LoadingPixelSkeleton className="h-20 w-full" />
        </div>
      </div>
    </div>
  );
}
