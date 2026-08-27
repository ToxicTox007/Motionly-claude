import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/button";
import { getAdminApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { promoteEditorialDataset, rollbackEditorialDeployment } from "./actions";

type Dataset = {
  id: string;
  status: string;
  item_count: number;
  manifest_hash: string;
  created_at: string;
};

type Deployment = {
  active_dataset_id?: string | null;
  previous_dataset_id?: string | null;
  state: string;
  revision: number;
  updated_at?: string | null;
};

async function editorialOperations(projectId: string) {
  const context = await getAdminApiContext();
  if (!context) redirect("/dashboard?error=admin_required");
  const headers = {
    ...context.headers,
    ...(process.env.ADMIN_API_TOKEN ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN } : {}),
  };
  const base = `${env.apiBaseUrl}/admin/editorial-learning/projects/${encodeURIComponent(projectId)}`;
  const [datasetsResponse, deploymentResponse] = await Promise.all([
    fetch(`${base}/datasets`, { cache: "no-store", headers }),
    fetch(`${base}/deployment`, { cache: "no-store", headers }),
  ]);
  if (!datasetsResponse.ok || !deploymentResponse.ok) {
    return { datasets: [] as Dataset[], deployment: null, error: "editorial_operations_unavailable" };
  }
  const datasetsPayload = await datasetsResponse.json();
  return {
    datasets: Array.isArray(datasetsPayload?.datasets) ? datasetsPayload.datasets as Dataset[] : [],
    deployment: await deploymentResponse.json() as Deployment,
    error: null,
  };
}

export default async function EditorialLearningOperationsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await searchParams) ?? {};
  const projectId = typeof params.projectId === "string" ? params.projectId.trim() : "";
  const result = typeof params.promoted === "string"
    ? "Dataset activated."
    : typeof params.rolled_back === "string"
      ? "Deployment rolled back."
      : null;
  const error = typeof params.error === "string" ? params.error.replaceAll("_", " ") : null;
  const operations = projectId ? await editorialOperations(projectId) : null;

  return (
    <AppShell title="Editorial operations">
      <div className="mx-auto max-w-5xl space-y-6">
        <section className="card-elevated p-6">
          <span className="badge badge-primary">Admin only</span>
          <h2 className="mt-3 text-3xl font-black tracking-tight">Editorial-learning operations</h2>
          <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
            Review only frozen, consented dataset metadata. Activation changes a project preference deployment for the current administrator; it never silently enables consent or exposes source feedback.
          </p>
          <form className="mt-5 flex max-w-2xl flex-col gap-3 sm:flex-row" method="get">
            <label className="flex-1">
              <span className="sr-only">Project ID</span>
              <input
                className="pixel-input h-11 w-full px-3"
                defaultValue={projectId}
                name="projectId"
                pattern="[0-9a-fA-F-]{36}"
                placeholder="Project UUID"
                required
              />
            </label>
            <Button type="submit">Load project</Button>
          </form>
        </section>

        {result ? <p role="status" className="rounded border border-success bg-success/10 p-4 text-sm font-semibold text-success">{result}</p> : null}
        {error ? <p role="alert" className="rounded border border-danger bg-danger/10 p-4 text-sm font-semibold text-danger">{error}</p> : null}
        {operations?.error ? <p role="alert" className="rounded border border-danger bg-danger/10 p-4 text-sm font-semibold text-danger">Editorial operations are unavailable or disabled for this deployment.</p> : null}

        {operations?.deployment ? (
          <section className="card rounded-xl p-6 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-black">Current deployment</h3>
                <p className="text-sm text-muted-foreground">State is revisioned to prevent stale activation decisions.</p>
              </div>
              <span className="badge badge-accent capitalize">{operations.deployment.state}</span>
            </div>
            <dl className="grid gap-3 text-sm sm:grid-cols-3">
              <Fact label="Active dataset" value={operations.deployment.active_dataset_id ?? "None"} />
              <Fact label="Previous dataset" value={operations.deployment.previous_dataset_id ?? "None"} />
              <Fact label="Revision" value={String(operations.deployment.revision)} />
            </dl>
            <form action={rollbackEditorialDeployment} className="rounded-lg border border-border bg-muted p-4 space-y-3">
              <input name="projectId" type="hidden" value={projectId} />
              <label className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Rollback reason
                <input className="pixel-input mt-1.5 h-10 w-full px-3 normal-case tracking-normal" defaultValue="admin_rollback" maxLength={160} name="reason" />
              </label>
              <label className="block text-xs text-muted-foreground">
                Type <strong>ROLLBACK</strong> to confirm
                <input className="pixel-input mt-1.5 h-10 w-full px-3" name="confirmation" required />
              </label>
              <Button type="submit" variant="secondary">Rollback deployment</Button>
            </form>
          </section>
        ) : null}

        {operations ? (
          <section className="card rounded-xl p-6 space-y-4">
            <div>
              <h3 className="text-lg font-black">Frozen consented datasets</h3>
              <p className="mt-1 text-sm text-muted-foreground">Only IDs, item counts, state, and immutable manifest hashes are shown.</p>
            </div>
            {operations.datasets.length ? operations.datasets.map((dataset) => (
              <article key={dataset.id} className="rounded-lg border border-border bg-muted p-4 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <code className="max-w-full truncate text-xs font-bold">{dataset.id}</code>
                  <span className="badge badge-secondary capitalize">{dataset.status}</span>
                </div>
                <dl className="grid gap-3 text-xs sm:grid-cols-3">
                  <Fact label="Items" value={String(dataset.item_count)} />
                  <Fact label="Created" value={new Date(dataset.created_at).toLocaleString()} />
                  <Fact label="Manifest" value={dataset.manifest_hash.slice(0, 16)} />
                </dl>
                {dataset.status === "frozen" || dataset.status === "shadow_passed" ? (
                  <form action={promoteEditorialDataset} className="flex flex-col gap-2 sm:flex-row sm:items-end">
                    <input name="projectId" type="hidden" value={projectId} />
                    <input name="datasetId" type="hidden" value={dataset.id} />
                    <input name="expectedRevision" type="hidden" value={operations.deployment?.revision ?? 0} />
                    <label className="flex-1 text-xs text-muted-foreground">
                      Type <strong>PROMOTE</strong> to activate this dataset
                      <input className="pixel-input mt-1.5 h-10 w-full px-3" name="confirmation" required />
                    </label>
                    <Button type="submit">Activate dataset</Button>
                  </form>
                ) : null}
              </article>
            )) : (
              <p className="rounded-lg border border-border bg-muted p-4 text-sm text-muted-foreground">No datasets are available for this project and administrator.</p>
            )}
          </section>
        ) : null}
      </div>
    </AppShell>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-1 truncate font-mono text-xs font-bold">{value}</dd>
    </div>
  );
}
