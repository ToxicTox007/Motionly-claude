import { redirect } from "next/navigation";
import { getWorkspaceApiContext } from "@/lib/api-auth";

/**
 * The workspace context for a signed-in page, or a redirect to log in.
 *
 * Pages used to fall back to `role ?? "owner"` when the context was missing
 * (Auth outage, a token expiring between the proxy and the render), which
 * showed management controls to a viewer whose role was unknown. A missing
 * context now fails closed: the viewer signs in again and lands back here.
 * (The skipAuthMiddleware local bypass still resolves to its mock owner.)
 */
export async function requireWorkspaceApiContext(returnTo: string) {
  const context = await getWorkspaceApiContext();
  if (!context) redirect(`/login?next=${encodeURIComponent(returnTo)}`);
  return context;
}
