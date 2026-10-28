"use client";

import { Button } from "@/components/button";
import { usePathname } from "next/navigation";
import { useId } from "react";

type WorkspaceOption = { id: string; name: string };

// A compact switcher shown in the app shell when the viewer belongs to more
// than one workspace. It posts to the same endpoint as the full-page picker
// (/app-api/workspaces/select), which pins the cookie and brings the viewer
// back to the page they were on. Choosing an option does not submit by itself
// (WCAG 3.2.2: arrowing through a closed select must not switch workspaces);
// the Switch button does, for mouse, keyboard and no-JS users alike.
export function WorkspaceSwitcher({
  workspaces,
  currentWorkspaceId,
}: {
  workspaces: WorkspaceOption[];
  currentWorkspaceId: string | null;
}) {
  const id = useId();
  const pathname = usePathname() ?? "/dashboard";
  if (workspaces.length < 2) return null;

  return (
    <form
      action="/app-api/workspaces/select"
      method="post"
      className="mb-4 space-y-1"
    >
      <input type="hidden" name="return_to" value={pathname} />
      <label htmlFor={id} className="px-3 text-xs font-semibold text-primary">
        Workspace
      </label>
      <div className="flex items-center gap-2">
        <select
          id={id}
          name="workspace_id"
          defaultValue={currentWorkspaceId ?? ""}
          className="min-h-10 w-full rounded-neu border border-control bg-surface px-2 text-xs font-semibold text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
        >
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name}
            </option>
          ))}
        </select>
        <Button type="submit" size="sm" className="shrink-0 px-3">
          Switch
        </Button>
      </div>
    </form>
  );
}
