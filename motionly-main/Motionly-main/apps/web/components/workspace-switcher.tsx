"use client";

import { useRef } from "react";

type WorkspaceOption = { id: string; name: string };

// A compact switcher shown in the app shell when the viewer belongs to more
// than one workspace. Changing the selection posts to the same endpoint as the
// full-page picker (/app-api/workspaces/select), which pins the cookie and
// redirects to the dashboard. No JavaScript is required to submit — the button
// is a real submit control for keyboard and no-JS users; `onChange` just
// submits sooner for mouse users.
export function WorkspaceSwitcher({
  workspaces,
  currentWorkspaceId,
}: {
  workspaces: WorkspaceOption[];
  currentWorkspaceId: string | null;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  if (workspaces.length < 2) return null;

  return (
    <form
      ref={formRef}
      action="/app-api/workspaces/select"
      method="post"
      className="mb-4 space-y-1"
    >
      <label
        htmlFor="workspace-switcher"
        className="px-3 text-xs font-semibold text-primary"
      >
        Workspace
      </label>
      <div className="flex items-center gap-2">
        <select
          id="workspace-switcher"
          name="workspace_id"
          defaultValue={currentWorkspaceId ?? ""}
          onChange={() => formRef.current?.requestSubmit()}
          className="min-h-10 w-full rounded-neu border border-control bg-surface px-2 text-xs font-semibold text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
        >
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name}
            </option>
          ))}
        </select>
        <button
          type="submit"
          className="min-h-10 shrink-0 rounded-neu bg-primary px-3 text-xs font-semibold text-primary-foreground shadow-neu-glow transition-shadow duration-200 hover:brightness-105 active:shadow-neu-pressed"
        >
          Switch
        </button>
      </div>
    </form>
  );
}
