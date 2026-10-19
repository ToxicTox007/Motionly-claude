"use client";

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
  return (
    <div className="mx-auto max-w-lg space-y-4 border-2 border-foreground bg-surface p-6 shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff]">
      <div>
        <h2 className="text-lg font-black uppercase tracking-tight">
          Select a workspace
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Choose the workspace for this session. Motionly will use this choice
          for API reads and mutations until you switch it.
        </p>
      </div>
      {error ? (
        <p
          role="alert"
          className="border-2 border-danger bg-danger/10 p-3 text-xs font-bold"
        >
          Workspace selection could not be applied. Choose an active workspace
          and try again.
        </p>
      ) : null}
      <form
        action="/app-api/workspaces/select"
        method="post"
        className="space-y-3"
      >
        <label
          className="block text-xs font-black uppercase"
          htmlFor="workspace_id"
        >
          Workspace
        </label>
        <select
          id="workspace_id"
          name="workspace_id"
          required
          className="min-h-11 w-full border-2 border-foreground bg-background px-3 text-sm font-bold"
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
        <button
          type="submit"
          className="min-h-11 border-2 border-foreground bg-primary px-4 text-xs font-black uppercase text-primary-foreground shadow-[2px_2px_0px_#000] hover:translate-x-px hover:translate-y-px hover:shadow-none"
        >
          Continue
        </button>
      </form>
    </div>
  );
}
