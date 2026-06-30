export default function ProjectDetailsLoading() {
  return (
    <div className="flex min-h-screen flex-col overflow-hidden bg-background">
      <div className="pixel-grid-fine flex min-h-12 items-center gap-3 border-b border-border bg-surface/95 px-4 py-2">
        <div className="h-6 w-24 animate-pulse bg-muted" />
        <div className="h-5 w-40 animate-pulse bg-muted" />
      </div>
      <div className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[220px_minmax(0,1fr)_360px] lg:grid-rows-[minmax(0,1fr)_190px]">
        <div className="pixel-panel order-2 min-h-[260px] animate-pulse bg-muted/40 lg:order-1 lg:min-h-0" />
        <div className="pixel-panel-strong order-1 min-h-[460px] animate-pulse bg-muted/40 lg:order-2 lg:min-h-0" />
        <div className="pixel-panel order-3 min-h-[420px] animate-pulse bg-muted/40 lg:order-3 lg:min-h-0" />
        <div className="pixel-panel order-4 min-h-[190px] animate-pulse bg-muted/40 lg:col-span-3 lg:min-h-0" />
      </div>
    </div>
  );
}
