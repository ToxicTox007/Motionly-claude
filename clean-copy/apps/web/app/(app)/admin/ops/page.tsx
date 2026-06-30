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
  provider_costs?: Record<string, number | string>;
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
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-base font-bold">Queues</h2>
          <ButtonLink href="/feedback" variant="secondary">
            Send feedback
          </ButtonLink>
        </div>
        <div className="pixel-panel overflow-hidden">
          <table className="w-full text-left text-sm">
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
    const response = await fetch(`${env.apiBaseUrl}/admin/queues`, {
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
      <div className="flex items-center gap-2 text-xs font-semibold uppercase text-muted-foreground">
        <Icon className="size-4" />
        {label}
      </div>
      <p className="mt-3 text-2xl font-bold">{value}</p>
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

function formatProviderCosts(
  costs: Record<string, number | string> | undefined,
): string {
  if (!costs) return "No cost data yet";

  const filteredCosts = Object.entries(costs).filter(([k, v]) => {
    const keyLower = k.toLowerCase();
    const valString = String(v ?? "").toLowerCase();
    return !keyLower.includes("placeholder") && !valString.includes("placeholder");
  });

  if (filteredCosts.length === 0) return "No cost data yet";

  let total = 0;
  let hasNumericCost = false;
  const costLines: string[] = [];

  for (const [k, v] of filteredCosts) {
    const num = Number(v);
    if (!isNaN(num) && v !== "" && v !== null) {
      total += num;
      hasNumericCost = true;
      costLines.push(`${k}: $${num.toFixed(2)}`);
    } else {
      costLines.push(`${k}: ${v}`);
    }
  }

  if (hasNumericCost) {
    return `$${total.toFixed(2)}`;
  }

  return costLines.join(", ");
}
