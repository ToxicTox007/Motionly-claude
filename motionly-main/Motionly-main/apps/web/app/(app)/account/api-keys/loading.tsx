import { AppShell } from "@/components/app-shell";
import {
  FormSkeleton,
  RouteSkeleton,
  TableSkeleton,
} from "@/components/route-skeleton";

export default function ApiKeysLoading() {
  return (
    <AppShell title="API Keys">
      <RouteSkeleton label="API keys">
        <FormSkeleton fields={1} />
        <TableSkeleton columns={4} rows={3} />
      </RouteSkeleton>
    </AppShell>
  );
}
