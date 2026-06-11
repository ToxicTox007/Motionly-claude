import { Activity, AlertTriangle, Gauge, RotateCcw } from "lucide-react";
import type { ComponentType } from "react";
import { AppShell } from "@/components/app-shell";
import { ButtonLink } from "@/components/button";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

type QueueSnapshot = {
  queue_name: string;
  queued: number;
  dead_letter: number;
  canceled: number;
  oldest_queued_seconds: number | null;
};

type RenderJob = {
  id: string;
  project_id: string;
  status: string;
  progress_pct: number;
  error_code: string | null;
  created_at: string;
};

type OpsOverview = {
  environment: string;
  request_id: string;
  queues: QueueSnapshot[];
  recent_renders: RenderJob[];
  launch_metrics: {
    recent_render_count?: number;
    recent_render_failures?: number;
    recent_render_completions?: number;
    render_success_rate?: number | null;
  };
};

export default async function LaunchOpsPage() {
  const context = await getWorkspaceApiContext();
  const overview = context ? await fetchOpsOverview(context.headers) : null;

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
      </div>

      <section className="mb-8">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-base font-bold">Queues</h2>
          <ButtonLink href="/feedback" variant="secondary">
            Send feedback
          </ButtonLink>
        </div>
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-4 py-3">Queue</th>
                <th className="px-4 py-3">Queued</th>
                <th className="px-4 py-3">Dead letter</th>
                <th className="px-4 py-3">Oldest</th>
              </tr>
            </thead>
            <tbody>
              {(overview?.queues ?? []).map((queue) => (
                <tr key={queue.queue_name} className="border-t border-border">
                  <td className="px-4 py-3 font-semibold">{queue.queue_name}</td>
                  <td className="px-4 py-3">{queue.queued}</td>
                  <td className="px-4 py-3">{queue.dead_letter}</td>
                  <td className="px-4 py-3">{formatAge(queue.oldest_queued_seconds)}</td>
                </tr>
              ))}
              {!overview?.queues.length ? (
                <tr>
                  <td className="px-4 py-8 text-center text-muted-foreground" colSpan={4}>
                    No queued jobs reported.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-base font-bold">Recent renders</h2>
        <div className="grid gap-3">
          {(overview?.recent_renders ?? []).map((render) => (
            <div
              key={render.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface p-4"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{render.id}</p>
                <p className="text-xs text-muted-foreground">
                  {render.status} | {render.progress_pct}% |{" "}
                  {new Date(render.created_at).toLocaleString()}
                </p>
              </div>
              <span className="inline-flex items-center gap-2 rounded-full bg-muted px-3 py-1 text-xs font-semibold text-muted-foreground">
                <RotateCcw className="size-3.5" aria-hidden="true" />
                {render.error_code ?? "healthy"}
              </span>
            </div>
          ))}
          {!overview?.recent_renders.length ? (
            <div className="rounded-lg border border-border bg-surface p-8 text-center text-sm text-muted-foreground">
              No render activity yet.
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
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase text-muted-foreground">
        <Icon className="size-4" />
        {label}
      </div>
      <p className="mt-3 text-2xl font-bold">{value}</p>
    </div>
  );
}

function formatAge(seconds: number | null) {
  if (seconds == null) return "None";
  if (seconds < 60) return `${seconds}s`;
  return `${Math.round(seconds / 60)}m`;
}
