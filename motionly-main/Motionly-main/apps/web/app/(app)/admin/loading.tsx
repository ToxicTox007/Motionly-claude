import { AppShell } from "@/components/app-shell";
import { LoadingPixelSkeleton } from "@/components/editor/ui";
import { RouteSkeleton, TableSkeleton } from "@/components/route-skeleton";

// Shared by the admin list pages (users, music, AI, editorial learning);
// admin/ops keeps its own dashboard-shaped skeleton.
export default function AdminLoading() {
  return (
    <AppShell title="Admin">
      <RouteSkeleton label="admin">
        <div className="flex flex-wrap items-end gap-3">
          <LoadingPixelSkeleton className="h-11 w-full max-w-sm" />
          <LoadingPixelSkeleton className="h-11 w-28" />
        </div>
        <TableSkeleton columns={5} rows={6} />
      </RouteSkeleton>
    </AppShell>
  );
}
