import { AppShell } from "@/components/app-shell";
import { LoadingPixelSkeleton } from "@/components/editor/ui";

export default function AccountLoading() {
  return (
    <AppShell title="Account">
      <div className="space-y-6">
        <section className="flex flex-col items-start gap-6 rounded-neu-lg bg-surface p-6 shadow-neu-raised sm:flex-row">
          <LoadingPixelSkeleton className="size-14 shrink-0 rounded-full" />
          <div className="min-w-0 flex-1 space-y-3">
            <LoadingPixelSkeleton className="h-6 w-24" />
            <LoadingPixelSkeleton className="h-9 w-64" />
            <LoadingPixelSkeleton className="h-4 w-full max-w-md" />
          </div>
          <div className="w-full shrink-0 space-y-2 sm:w-56">
            <LoadingPixelSkeleton className="ml-auto h-4 w-32" />
            <LoadingPixelSkeleton className="ml-auto h-3 w-44" />
            <LoadingPixelSkeleton className="ml-auto h-6 w-20 rounded-full" />
          </div>
        </section>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="space-y-5 rounded-neu-lg bg-surface p-6 shadow-neu-raised">
            <div className="flex items-center gap-2">
              <LoadingPixelSkeleton className="size-5" />
              <LoadingPixelSkeleton className="h-6 w-36" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <FieldSkeleton />
              <FieldSkeleton />
            </div>
            <FieldSkeleton wide />
            <LoadingPixelSkeleton className="h-11 w-36" />
          </div>

          <aside className="space-y-5">
            {[1, 2].map((item) => (
              <div
                key={item}
                className="space-y-4 rounded-neu-lg bg-surface p-5 shadow-neu-raised"
              >
                <LoadingPixelSkeleton className="h-6 w-32" />
                <LoadingPixelSkeleton className="h-14 w-full" />
                <LoadingPixelSkeleton className="h-14 w-full" />
                {item === 2 ? (
                  <LoadingPixelSkeleton className="h-11 w-full" />
                ) : null}
              </div>
            ))}
          </aside>
        </div>

        <div className="max-w-2xl space-y-5 rounded-neu-lg bg-surface p-6 shadow-neu-raised">
          <div className="flex items-center gap-2">
            <LoadingPixelSkeleton className="size-5" />
            <LoadingPixelSkeleton className="h-6 w-28" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <FieldSkeleton />
            <FieldSkeleton />
          </div>
          <LoadingPixelSkeleton className="h-11 w-40" />
        </div>
      </div>
    </AppShell>
  );
}

function FieldSkeleton({ wide = false }: { wide?: boolean }) {
  return (
    <div className={wide ? "space-y-2" : "space-y-2 min-w-0"}>
      <LoadingPixelSkeleton className="h-3 w-24" />
      <LoadingPixelSkeleton className="h-11 w-full" />
    </div>
  );
}
