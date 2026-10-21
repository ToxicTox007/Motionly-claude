import { Suspense } from "react";
import { PageTitle } from "@/components/page-title";
import { InviteClient } from "./InviteClient";

// Public page: a teammate follows an invite link `/invite#token=...`. The token
// lives only in the URL fragment, so it must be read in the browser (the server
// never sees a fragment). The client component handles the signed-in / signed-out
// flows and the accept call.
export default function InvitePage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-6 py-16">
      <PageTitle title="Join a workspace" />
      <Suspense fallback={null}>
        <InviteClient />
      </Suspense>
    </main>
  );
}
