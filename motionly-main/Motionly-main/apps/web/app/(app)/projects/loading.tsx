import { AppShell } from "@/components/app-shell";
import { LoadingPixelSkeleton } from "@/components/editor/ui";

export default function ProjectsLoading() {
  return (
    <AppShell title="Projects">
      <div className="space-y-6">
        <section className="flex flex-col gap-4 rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg sm:flex-row sm:items-center sm:justify-between sm:p-8">
          <div className="min-w-0 space-y-2">
            <LoadingPixelSkeleton className="h-4 w-32" />
            <LoadingPixelSkeleton className="h-8 w-48" />
            <LoadingPixelSkeleton className="h-4 w-full max-w-lg" />
          </div>
          <LoadingPixelSkeleton className="h-12 w-40 shrink-0" />
        </section>

        <div className="grid gap-4">
          {[1, 2, 3, 4].map((project) => (
            <article
              key={project}
              className="rounded-neu-lg bg-surface p-5 shadow-neu-raised"
            >
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px_110px] lg:items-center">
                <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_150px_auto] sm:items-end">
                  <div className="min-w-0 space-y-2">
                    <LoadingPixelSkeleton className="h-3 w-16" />
                    <LoadingPixelSkeleton className="h-11 w-full" />
                  </div>
                  <div className="space-y-2">
                    <LoadingPixelSkeleton className="h-3 w-16" />
                    <LoadingPixelSkeleton className="h-11 w-full" />
                  </div>
                  <LoadingPixelSkeleton className="h-11 w-24" />
                </div>

                <div className="space-y-2 lg:justify-self-end">
                  <LoadingPixelSkeleton className="h-4 w-36" />
                  <LoadingPixelSkeleton className="h-6 w-20 rounded-full lg:ml-auto" />
                </div>

                <div className="flex gap-2 lg:justify-end">
                  <LoadingPixelSkeleton className="h-10 flex-1 lg:w-16 lg:flex-none" />
                  <LoadingPixelSkeleton className="h-10 w-11" />
                </div>
              </div>
            </article>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
