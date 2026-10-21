import { WatchClient } from "./WatchClient";
import { PageTitle } from "@/components/page-title";

// The share token is only ever in the URL fragment (#t=...), which browsers do
// not send to the server, so this page is rendered without it and the client
// reads the token and resolves the export.
export default function WatchPage() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <PageTitle title="Watch video" />
      <WatchClient />
    </div>
  );
}
