"use client";

import { useState } from "react";
import { Button } from "@/components/button";

type WorkspaceOption = {
  id: string;
  name: string;
};

export function WorkspacePicker({
  workspaces,
  error,
}: {
  workspaces: WorkspaceOption[];
  error?: string | null;
}) {
  // A plain POST to the route handler (it sets an httpOnly cookie), so
  // useFormStatus does not apply: disable the button once it is submitted.
  const [submitting, setSubmitting] = useState(false);
  return (
    <div className="mx-auto max-w-lg space-y-4 rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg sm:p-8">
      <div>
        <h2 className="text-lg font-bold tracking-tight text-foreground">
          Select a workspace
        </h2>
        <p className="mt-2 text-sm font-medium text-muted-foreground leading-relaxed">
          Choose the workspace for this session. Motionly will use this choice
          for API reads and mutations until you switch it.
        </p>
      </div>
      {error ? (
        <p
          role="alert"
          className="rounded-neu-md bg-surface p-3 text-xs font-semibold text-danger shadow-neu-inset-sm ring-1 ring-danger/30"
        >
          Workspace selection could not be applied. Choose an active workspace
          and try again.
        </p>
      ) : null}
      <form
        action="/app-api/workspaces/select"
        method="post"
        className="space-y-3"
        onSubmit={(event) => {
          if (submitting) event.preventDefault();
          else setSubmitting(true);
        }}
      >
        <label
          className="block text-xs font-semibold text-foreground"
          htmlFor="workspace_id"
        >
          Workspace
        </label>
        <select
          id="workspace_id"
          name="workspace_id"
          required
          className="min-h-11 w-full rounded-neu border border-control bg-surface px-3 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          defaultValue=""
        >
          <option value="" disabled>
            Choose a workspace
          </option>
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name}
            </option>
          ))}
        </select>
        <Button type="submit" disabled={submitting} aria-busy={submitting}>
          {submitting ? "Switching…" : "Continue"}
        </Button>
      </form>
    </div>
  );
}
