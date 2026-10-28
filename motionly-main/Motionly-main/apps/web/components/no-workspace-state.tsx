import { Users } from "lucide-react";
import { logout } from "@/app/(auth)/actions";
import { Button } from "@/components/button";

/**
 * The viewer belongs to no active workspace (for example after leaving or
 * being removed from their only one). Every workspace screen needs one, so
 * explain how to get back in rather than redirecting in a loop.
 */
export function NoWorkspaceState() {
  return (
    <section
      aria-labelledby="no-workspace-title"
      className="mx-auto max-w-lg rounded-neu-xl bg-surface p-8 text-center shadow-neu-raised-lg"
    >
      <div className="mx-auto mb-6 flex size-16 items-center justify-center rounded-neu-md bg-primary shadow-neu-glow">
        <Users
          className="size-7 text-primary-foreground"
          aria-hidden="true"
          strokeWidth={2}
        />
      </div>
      <h2
        id="no-workspace-title"
        className="text-xl font-bold tracking-tight text-foreground"
      >
        You are not in a workspace
      </h2>
      <p className="mx-auto mt-2 max-w-sm text-sm font-medium leading-relaxed text-muted-foreground">
        Your account is not an active member of any workspace, so there are no
        projects to show. Ask a workspace owner or admin for an invite link and
        open it while signed in to join their workspace.
      </p>
      <form action={logout} className="mt-6">
        <Button type="submit" variant="secondary">
          Sign out
        </Button>
      </form>
    </section>
  );
}
