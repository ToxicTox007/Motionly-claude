import { AppShell } from "@/components/app-shell";
import { messageFor, searchParam } from "@/lib/error-messages";
import { NewProjectClient } from "./new-project-client";

export default async function NewProjectPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const errorCode = searchParam((await searchParams) ?? {}, "error");
  return (
    <AppShell title="Create New Reel">
      <NewProjectClient
        serverError={errorCode ? messageFor("projects", errorCode) : null}
      />
    </AppShell>
  );
}
