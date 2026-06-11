import { UserRound } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/button";
import { EmptyState } from "@/components/empty-state";
import { logout } from "../../(auth)/actions";

export default function AccountPage() {
  return (
    <AppShell title="Account">
      <EmptyState
        icon={UserRound}
        title="Account settings"
        body="Profile, logout, and reset-password flows will connect to Supabase auth from this Week 1 foundation."
      />
      <form action={logout} className="mt-6">
        <Button type="submit" variant="secondary">
          Log out
        </Button>
      </form>
    </AppShell>
  );
}
