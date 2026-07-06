import {
  Activity,
  AlertTriangle,
  Gauge,
  RotateCcw,
  Square,
  WalletCards,
} from "lucide-react";
import { redirect } from "next/navigation";
import type { ComponentType } from "react";
import { AppShell } from "@/components/app-shell";
import { ButtonLink } from "@/components/button";
import { getAdminApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { AdminJobsList } from "./AdminJobsList";
import {
  cancelQueueJob,
  cancelRenderJob,
  retryQueueJob,
  retryRenderJob,
} from "./actions";

type QueueSnapshot = {
  queue_name: string;
  queued: number;
  dead_letter: number;
  canceled: number;
  running: number;
  succeeded: number;
  failed: number;
  oldest_queued_seconds: number | null;
};

type RenderJob = {
  id: string;
  project_id: string;
  status: string;
  progress_pct: number;
  error_code: string | null;
  error_message?: string | null;
  logs?: string[];
  created_at: string;
};

type QueueJob = {
  id: string;
  kind: string;
  queue_name: string;
  status: string;
  attempts: number;
  max_attempts: number;
  project_id?: string | null;
  resource_id?: string | null;
};

type ProviderSloSummary = {
  provider: string;
  operation: string;
  total: number;
  successes: number;
  errors: number;
  success_rate: number | null;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  last_error_code: string | null;
};

type ProviderCostSummary = {
  currency?: string;
  total_usd?: number;
  by_category?: Record<string, number>;
  by_provider?: Record<string, number>;
  approved_exports?: number;
  cost_per_approved_export_usd?: number | null;
};

type OpsDashboard = {
  api_p95_ms?: number | null;
  api_p99_ms?: number | null;
  route_errors_5xx?: number;
  route_errors_by_route?: Record<string, number>;
  supabase_p95_ms?: number | null;
  slow_supabase_calls?: number;
  max_queue_age_seconds?: number | null;
  gpu_backlog?: number;
  fresh_worker_count?: number;
  stale_worker_count?: number;
  render_latency_p95_ms?: number | null;
  export_latency_p95_ms?: number | null;
};

type OpsOverview = {
  environment: string;
  request_id: string;
  queues: QueueSnapshot[];
  jobs?: QueueJob[];
  recent_renders: RenderJob[];
  launch_metrics: {
    recent_render_count?: number;
    recent_render_failures?: number;
    recent_render_completions?: number;
    render_success_rate?: number | null;
  };
  operations_dashboard?: OpsDashboard;
  provider_costs?: ProviderCostSummary;
  provider_slos?: ProviderSloSummary[];
  render_failure_diagnostics?: Array<Record<string, unknown>>;
  health?: Record<string, unknown>;
};

export default async function LaunchOpsPage() {
  const context = await getAdminApiContext();
  if (!context) redirect("/dashboard?error=admin_access_denied");
  const [overview, queues, dependencyHealth] = await Promise.all([
    fetchOpsOverview(context.headers),
    fetchQueues(context.headers),
    fetchDependencyHealth(context.headers),
  ]);
  const providerCostEntries = sanitizeProviderCostEntries(
    overview?.provider_costs?.by_provider,
  );

  return (
    <AppShell title="Launch Ops">
      <div className="mb-6 grid gap-3 md:grid-cols-3">
        <MetricCard
          icon={Gauge}
          label="Render success"
          value={
            overview?.launch_metrics.render_success_rate == null
              ? "No data"
              : `${Math.round(overview.launch_metrics.render_success_rate * 100)}%`
          }
        />
        <MetricCard
          icon={Activity}
          label="Recent renders"
          value={String(overview?.launch_metrics.recent_render_count ?? 0)}
        />
        <MetricCard
          icon={AlertTriangle}
          label="Failures"
          value={String(overview?.launch_metrics.recent_render_failures ?? 0)}
        />
        <MetricCard
          icon={WalletCards}
          label="Cost tracking"
          value={formatProviderCosts(overview?.provider_costs)}
        />
      </div>

      <section className="mb-8">
        <h2 className="mb-3 text-base font-bold">Runtime signals</h2>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            icon={Gauge}
            label="API p95"
            value={formatLatency(overview?.operations_dashboard?.api_p95_ms ?? null)}
          />
          <MetricCard
            icon={Gauge}
            label="API p99"
            value={formatLatency(overview?.operations_dashboard?.api_p99_ms ?? null)}
          />
          <MetricCard
            icon={AlertTriangle}
            label="Route errors"
            value={String(overview?.operations_dashboard?.route_errors_5xx ?? 0)}
          />
          <MetricCard
            icon={Activity}
            label="Supabase p95"
            value={formatLatency(overview?.operations_dashboard?.supabase_p95_ms ?? null)}
          />
          <MetricCard
            icon={AlertTriangle}
            label="Slow Supabase"
            value={String(overview?.operations_dashboard?.slow_supabase_calls ?? 0)}
          />
          <MetricCard
            icon={Activity}
            label="Max queue age"
            value={formatAge(overview?.operations_dashboard?.max_queue_age_seconds ?? null)}
          />
          <MetricCard
            icon={Gauge}
            label="GPU backlog"
            value={String(overview?.operations_dashboard?.gpu_backlog ?? 0)}
          />
          <MetricCard
            icon={Activity}
            label="Worker freshness"
            value={formatWorkerFreshness(overview?.operations_dashboard)}
          />
          <MetricCard
            icon={Gauge}
            label="Render p95"
            value={formatLatency(
              overview?.operations_dashboard?.render_latency_p95_ms ?? null,
            )}
          />
          <MetricCard
            icon={Gauge}
            label="Export p95"
            value={formatLatency(
              overview?.operations_dashboard?.export_latency_p95_ms ?? null,
            )}
          />
        </div>
      </section>

      <section className="mb-8 grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div>
          <h2 className="mb-3 text-base font-bold">Provider SLOs</h2>
          <div className="pixel-panel overflow-x-auto">
            <table className="min-w-[760px] w-full text-left text-sm">
              <thead className="bg-muted text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-4 py-3">Provider</th>
                  <th className="px-4 py-3">Operation</th>
                  <th className="px-4 py-3">Success</th>
                  <th className="px-4 py-3">Errors</th>
                  <th className="px-4 py-3">P95</th>
                  <th className="px-4 py-3">Last error</th>
                </tr>
              </thead>
              <tbody>
                {(overview?.provider_slos ?? []).map((slo) => (
                  <tr
                    key={`${slo.provider}:${slo.operation}`}
                    className="border-t border-border"
                  >
                    <td className="px-4 py-3 font-semibold">{slo.provider}</td>
                    <td className="px-4 py-3">{slo.operation}</td>
                    <td className="px-4 py-3">
                      {formatPercent(slo.success_rate)}
                    </td>
                    <td className="px-4 py-3">{slo.errors}</td>
                    <td className="px-4 py-3">
                      {formatLatency(slo.p95_latency_ms)}
                    </td>
                    <td className="px-4 py-3">
                      {slo.last_error_code ?? "None"}
                    </td>
                  </tr>
                ))}
                {!overview?.provider_slos?.length ? (
                  <tr>
                    <td
                      className="px-4 py-8 text-center text-muted-foreground"
                      colSpan={6}
                    >
                      No provider SLO samples reported.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          <h2 className="mb-3 text-base font-bold">Provider costs</h2>
          <div className="pixel-panel p-4">
            <p className="text-xs font-black uppercase tracking-widest text-muted-foreground">
              Total tracked
            </p>
            <p className="mt-2 text-2xl font-black">
              {formatUsd(overview?.provider_costs?.total_usd)}
            </p>
            <p className="mt-1 text-xs font-semibold text-muted-foreground">
              Cost per approved export:{" "}
              {formatUsd(overview?.provider_costs?.cost_per_approved_export_usd)}
            </p>
            <div className="mt-4 grid gap-2">
              {providerCostEntries.map(([provider, amount]) => (
                  <div
                    key={provider}
                    className="flex items-center justify-between gap-3 border-t border-border pt-2 text-sm"
                  >
                    <span className="font-semibold">{provider}</span>
                    <span>{formatUsd(amount)}</span>
                  </div>
                ))}
              {!providerCostEntries.length ? (
                <p className="border-t border-border pt-3 text-sm text-muted-foreground">
                  No provider costs reported.
                </p>
              ) : null}
            </div>
          </div>
        </div>
      </section>

      <section className="mb-8">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-base font-bold">Queues</h2>
          <ButtonLink href="/feedback" variant="secondary">
            Send feedback
          </ButtonLink>
        </div>
        <div className="pixel-panel overflow-x-auto">
          <table className="min-w-[760px] w-full text-left text-sm">
            <thead className="bg-muted text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-4 py-3">Queue</th>
                <th className="px-4 py-3">Queued</th>
                <th className="px-4 py-3">Running</th>
                <th className="px-4 py-3">Succeeded</th>
                <th className="px-4 py-3">Failed</th>
                <th className="px-4 py-3">Dead letter</th>
                <th className="px-4 py-3">Oldest</th>
              </tr>
            </thead>
            <tbody>
              {(overview?.queues ?? []).map((queue) => (
                <tr key={queue.queue_name} className="border-t border-border">
                  <td className="px-4 py-3 font-semibold">
                    {queue.queue_name}
                  </td>
                  <td className="px-4 py-3">{queue.queued}</td>
                  <td className="px-4 py-3">{queue.running}</td>
                  <td className="px-4 py-3">{queue.succeeded}</td>
                  <td className="px-4 py-3">{queue.failed}</td>
                  <td className="px-4 py-3">{queue.dead_letter}</td>
                  <td className="px-4 py-3">
                    {formatAge(queue.oldest_queued_seconds)}
                  </td>
                </tr>
              ))}
              {!overview?.queues.length ? (
                <tr>
                  <td
                    className="px-4 py-8 text-center text-muted-foreground"
                    colSpan={7}
                  >
                    No queued jobs reported.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mb-8">
        <h2 className="mb-3 text-base font-bold">Queue jobs</h2>
        <AdminJobsList
          jobs={queues?.jobs ?? []}
          totalJobs={queues?.total_jobs ?? 0}
          limit={queues?.limit ?? 50}
          onRetry={retryQueueJob}
          onCancel={cancelQueueJob}
        />
      </section>

      <section>
        <h2 className="mb-3 text-base font-bold">Recent renders</h2>
        <div className="grid gap-3">
          {(overview?.recent_renders ?? []).map((render) => (
            <div
              key={render.id}
              className="pixel-panel flex flex-wrap items-center justify-between gap-3 p-4"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{render.id}</p>
                <p className="text-xs text-muted-foreground">
                  {render.status} | {render.progress_pct}% |{" "}
                  {new Date(render.created_at).toLocaleString()}
                </p>
              </div>
              <span className="inline-flex items-center gap-2 border border-border bg-muted px-3 py-1 text-xs font-semibold text-muted-foreground">
                <RotateCcw className="size-3.5" aria-hidden="true" />
                {render.error_code ?? "healthy"}
              </span>
              <div className="flex gap-2">
                {["failed", "canceled"].includes(render.status) ? (
                  <ActionForm
                    action={retryRenderJob}
                    jobId={render.id}
                    label="Retry"
                    icon={RotateCcw}
                  />
                ) : null}
                {[
                  "queued",
                  "processing",
                  "preparing",
                  "rendering",
                  "uploading",
                ].includes(render.status) ? (
                  <ActionForm
                    action={cancelRenderJob}
                    jobId={render.id}
                    label="Cancel"
                    icon={Square}
                  />
                ) : null}
              </div>
            </div>
          ))}
          {!overview?.recent_renders.length ? (
            <div className="pixel-panel p-8 text-center text-sm text-muted-foreground">
              No render activity yet.
            </div>
          ) : null}
        </div>
      </section>

      <section className="mt-8">
        <h2 className="mb-3 text-base font-bold">Health checks</h2>
        <div className="grid gap-3 md:grid-cols-2">
          {Object.entries(
            dependencyHealth?.dependencies ?? overview?.health ?? {},
          ).map(([name, value]) => (
            <div key={name} className="pixel-panel p-4">
              <p className="text-xs font-black uppercase tracking-widest text-muted-foreground">
                {name}
              </p>
              <p className="mt-2 text-sm font-semibold">
                {typeof value === "object" && value && "status" in value
                  ? String((value as { status: unknown }).status)
                  : String(value)}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className="mt-8">
        <h2 className="mb-3 text-base font-bold">Render failure diagnostics</h2>
        <div className="grid gap-3">
          {(overview?.render_failure_diagnostics ?? []).map((diagnostic) => (
            <div
              key={String(diagnostic.render_job_id)}
              className="pixel-panel p-4"
            >
              <p className="truncate text-sm font-semibold">
                {String(diagnostic.render_job_id)}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {String(diagnostic.error_code ?? "unknown")} |{" "}
                {String(
                  diagnostic.error_message ??
                    diagnostic.last_log ??
                    "No diagnostic message recorded",
                )}
              </p>
            </div>
          ))}
          {!overview?.render_failure_diagnostics?.length ? (
            <div className="pixel-panel p-8 text-center text-sm text-muted-foreground">
              No recent render failures.
            </div>
          ) : null}
        </div>
      </section>
    </AppShell>
  );
}

async function fetchOpsOverview(headers: Record<string, string>) {
  try {
    const response = await fetch(`${env.apiBaseUrl}/admin/ops/overview`, {
      cache: "no-store",
      headers: {
        ...headers,
        ...(process.env.ADMIN_API_TOKEN
          ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN }
          : {}),
      },
    });
    if (!response.ok) return null;
    return (await response.json()) as OpsOverview;
  } catch {
    return null;
  }
}

async function fetchQueues(headers: Record<string, string>) {
  try {
    const response = await fetch(`${env.apiBaseUrl}/admin/queues?limit=50`, {
      cache: "no-store",
      headers: {
        ...headers,
        ...(process.env.ADMIN_API_TOKEN
          ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN }
          : {}),
      },
    });
    if (!response.ok) return null;
    return (await response.json()) as {
      queues: QueueSnapshot[];
      jobs: QueueJob[];
      total_jobs?: number;
      limit?: number;
    };
  } catch {
    return null;
  }
}

async function fetchDependencyHealth(headers: Record<string, string>) {
  try {
    const response = await fetch(`${env.apiBaseUrl}/health/dependencies`, {
      cache: "no-store",
      headers,
    });
    if (!response.ok) return null;
    return (await response.json()) as {
      dependencies: Record<string, { status: string; message: string }>;
    };
  } catch {
    return null;
  }
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="pixel-panel p-4">
      <div className="flex items-center gap-2 text-xs font-black uppercase text-primary">
        <Icon className="size-4" />
        {label}
      </div>
      <p className="mt-3 break-words text-2xl font-black">{value}</p>
    </div>
  );
}

function ActionForm({
  action,
  jobId,
  label,
  icon: Icon,
}: {
  action: (formData: FormData) => Promise<void>;
  jobId: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
}) {
  return (
    <form action={action}>
      <input type="hidden" name="jobId" value={jobId} />
      <button
        type="submit"
        className="pixel-button inline-flex h-9 items-center gap-2 bg-background px-3 text-xs font-bold"
      >
        <Icon className="size-3.5" aria-hidden="true" />
        {label}
      </button>
    </form>
  );
}

function formatAge(seconds: number | null) {
  if (seconds == null) return "None";
  if (seconds < 60) return `${seconds}s`;
  return `${Math.round(seconds / 60)}m`;
}

function formatProviderCosts(costs: ProviderCostSummary | undefined): string {
  if (!costs || costs.total_usd == null) return "No cost data yet";
  return formatUsd(costs.total_usd);
}

function sanitizeProviderCostEntries(
  byProvider: Record<string, number> | undefined,
): Array<[string, number]> {
  return Object.entries(byProvider ?? {}).filter(([key, value]) => {
    const keyLower = key.toLowerCase();
    const valString = String(value).toLowerCase();
    return !keyLower.includes("placeholder") && !valString.includes("placeholder");
  });
}

function formatUsd(value: number | null | undefined): string {
  if (value == null) return "No data";
  return `$${value.toFixed(2)}`;
}

function formatPercent(value: number | null): string {
  if (value == null) return "No data";
  return `${Math.round(value * 100)}%`;
}

function formatLatency(value: number | null): string {
  if (value == null) return "No data";
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(1)}s`;
}

function formatWorkerFreshness(dashboard: OpsDashboard | undefined): string {
  if (!dashboard) return "No data";
  return `${dashboard.fresh_worker_count ?? 0} fresh / ${
    dashboard.stale_worker_count ?? 0
  } stale`;
}
