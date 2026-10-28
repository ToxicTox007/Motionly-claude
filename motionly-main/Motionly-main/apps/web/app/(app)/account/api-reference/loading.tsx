import { AppShell } from "@/components/app-shell";
import {
  CardSkeleton,
  RouteSkeleton,
  TableSkeleton,
} from "@/components/route-skeleton";

export default function ApiReferenceLoading() {
  return (
    <AppShell title="API Reference">
      <RouteSkeleton label="the API reference">
        <CardSkeleton lines={3} />
        <TableSkeleton columns={3} rows={6} />
      </RouteSkeleton>
    </AppShell>
  );
}
