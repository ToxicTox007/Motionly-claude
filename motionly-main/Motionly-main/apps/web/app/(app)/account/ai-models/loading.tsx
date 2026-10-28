import { AppShell } from "@/components/app-shell";
import { CardSkeleton, RouteSkeleton } from "@/components/route-skeleton";

export default function AiModelsLoading() {
  return (
    <AppShell title="AI models">
      <RouteSkeleton label="AI models">
        <CardSkeleton lines={1} />
        <div className="grid gap-4 md:grid-cols-2">
          {[1, 2, 3, 4].map((item) => (
            <CardSkeleton key={item} lines={2} action />
          ))}
        </div>
      </RouteSkeleton>
    </AppShell>
  );
}
