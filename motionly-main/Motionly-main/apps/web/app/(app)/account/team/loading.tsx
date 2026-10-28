import { AppShell } from "@/components/app-shell";
import {
  CardSkeleton,
  FormSkeleton,
  RouteSkeleton,
  TableSkeleton,
} from "@/components/route-skeleton";

export default function TeamLoading() {
  return (
    <AppShell title="Team">
      <RouteSkeleton label="team">
        <CardSkeleton lines={1} />
        <FormSkeleton fields={2} />
        <TableSkeleton columns={4} rows={3} />
      </RouteSkeleton>
    </AppShell>
  );
}
