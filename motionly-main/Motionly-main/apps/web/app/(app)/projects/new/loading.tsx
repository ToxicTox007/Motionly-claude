import { AppShell } from "@/components/app-shell";
import { LoadingPixelSkeleton } from "@/components/editor/ui";
import { FormSkeleton, RouteSkeleton } from "@/components/route-skeleton";

// The create form, not the projects list it would otherwise inherit.
export default function NewProjectLoading() {
  return (
    <AppShell title="Create New Reel">
      <RouteSkeleton label="the new project form">
        <div className="mx-auto max-w-2xl space-y-6">
          <div className="space-y-3">
            <LoadingPixelSkeleton className="h-9 w-64" />
            <LoadingPixelSkeleton className="h-4 w-full max-w-md" />
          </div>
          <FormSkeleton fields={1} />
        </div>
      </RouteSkeleton>
    </AppShell>
  );
}
