import { AppShell } from "@/components/app-shell";
import { NewProjectClient } from "./new-project-client";

export default function NewProjectPage() {
  return (
    <AppShell title="Create New Reel">
      <NewProjectClient />
    </AppShell>
  );
}
