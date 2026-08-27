"use client";

import { BarChart3, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/button";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";

type PublishPlatform = "tiktok" | "instagram" | "youtube";

type PerformanceFeedbackStatus = {
  enabled: boolean;
  policy_version: string;
  default_retention_days: number;
  minimum_examples: number;
  allowed_metrics: string[];
  excluded_data: string[];
};

type PerformanceFeedbackPolicy = {
  metrics_ingestion_enabled: boolean;
  suggestions_enabled: boolean;
  policy_version: string;
  revision: number;
  retention_days: number;
  revoked_at?: string | null;
};

type PerformanceSuggestion = {
  kind: "captions" | "hook_style" | "duration";
  recommended_value: string;
  editor_instruction: string;
  sample_count: number;
  average_retention_rate: number;
  confidence: string;
};

type PerformanceInsights = {
  status: "feature_disabled" | "consent_required" | "suggestions_disabled" | "collecting" | "ready";
  minimum_examples: number;
  suggestions: PerformanceSuggestion[];
  data_boundaries: string[];
};

interface PerformanceFeedbackPanelProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  platform: PublishPlatform;
}

export function PerformanceFeedbackPanel({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  platform,
}: PerformanceFeedbackPanelProps) {
  const [status, setStatus] = useState<PerformanceFeedbackStatus | null>(null);
  const [policy, setPolicy] = useState<PerformanceFeedbackPolicy | null>(null);
  const [insights, setInsights] = useState<PerformanceInsights | null>(null);
  const [metricsEnabled, setMetricsEnabled] = useState(false);
  const [suggestionsEnabled, setSuggestionsEnabled] = useState(false);
  const [retentionDays, setRetentionDays] = useState(90);
  const [acknowledged, setAcknowledged] = useState(false);
  const [revokeConfirmed, setRevokeConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const options = useCallback(
    () => ({ workspaceId, userId, accessToken: apiAccessToken }),
    [apiAccessToken, userId, workspaceId],
  );

  const load = useCallback(async () => {
    const requestOptions = options();
    const [nextStatus, nextPolicy, nextInsights] = await Promise.all([
      editorApiRequest<PerformanceFeedbackStatus>(
        `${env.apiBaseUrl}/projects/${projectId}/performance-feedback/status`,
        requestOptions,
      ),
      editorApiRequest<PerformanceFeedbackPolicy>(
        `${env.apiBaseUrl}/projects/${projectId}/performance-feedback/policy`,
        requestOptions,
      ),
      editorApiRequest<PerformanceInsights>(
        `${env.apiBaseUrl}/projects/${projectId}/performance-feedback/insights?platform=${platform}`,
        requestOptions,
      ),
    ]);
    setStatus(nextStatus);
    setPolicy(nextPolicy);
    setInsights(nextInsights);
    setMetricsEnabled(nextPolicy.metrics_ingestion_enabled);
    setSuggestionsEnabled(nextPolicy.suggestions_enabled);
    setRetentionDays(nextPolicy.retention_days || nextStatus.default_retention_days);
  }, [options, platform, projectId]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void load().catch((error) => {
        if (!cancelled) setMessage((error as Error).message);
      });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [load]);

  async function saveConsent() {
    if (!status || !acknowledged) {
      setMessage("Acknowledge the data boundary before saving consent.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const next = await editorApiRequest<PerformanceFeedbackPolicy>(
        `${env.apiBaseUrl}/projects/${projectId}/performance-feedback/policy`,
        {
          ...options(),
          method: "PUT",
          body: {
            metrics_ingestion_enabled: metricsEnabled,
            suggestions_enabled: suggestionsEnabled,
            retention_days: retentionDays,
            policy_version: status.policy_version,
            expected_revision: policy?.revision ?? 0,
          },
          friendlyError: "Performance feedback consent could not be saved.",
        },
      );
      setPolicy(next);
      setAcknowledged(false);
      await load();
      setMessage(
        metricsEnabled
          ? "Performance-feedback consent saved. Only future aggregate snapshots are eligible."
          : "Performance-metric ingestion remains off.",
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!revokeConfirmed) {
      setMessage("Confirm deletion before withdrawing consent.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const next = await editorApiRequest<PerformanceFeedbackPolicy>(
        `${env.apiBaseUrl}/projects/${projectId}/performance-feedback/revoke`,
        {
          ...options(),
          method: "POST",
          body: { reason: "export_settings_withdrawal" },
          friendlyError: "Performance-feedback consent could not be withdrawn.",
        },
      );
      setPolicy(next);
      setMetricsEnabled(false);
      setSuggestionsEnabled(false);
      setRevokeConfirmed(false);
      await load();
      setMessage("Consent withdrawn and all stored performance snapshots were deleted.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const enabled = Boolean(policy?.metrics_ingestion_enabled && !policy.revoked_at);

  return (
    <section className="space-y-3 rounded-md border-2 border-foreground bg-surface p-4 shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff]" aria-labelledby="performance-feedback-heading">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <BarChart3 className="size-4" aria-hidden="true" />
            <h3 id="performance-feedback-heading" className="text-sm font-black uppercase">Performance feedback</h3>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Turn consented aggregate post performance into cautious editing suggestions for {platform}.
          </p>
        </div>
        <span className="rounded border border-foreground px-2 py-1 text-[10px] font-bold uppercase">
          {status?.enabled ? (enabled ? "consented" : "off") : "unavailable"}
        </span>
      </div>

      <div className="rounded border border-foreground/30 bg-muted/40 p-3 text-[11px] leading-relaxed text-muted-foreground">
        <strong className="text-foreground">Data boundary:</strong> only aggregate views, average view duration, completion rate, and engagement rate are accepted. We never ingest viewer identities, demographics, comments, messages, post copy, transcript text, prompts, source media, raw provider payloads, or tokens.
      </div>

      {status?.enabled ? (
        <div className="space-y-3">
          <label className="flex items-start gap-2 text-xs">
            <input checked={metricsEnabled} disabled={busy} onChange={(event) => setMetricsEnabled(event.target.checked)} type="checkbox" />
            <span><strong>Allow aggregate performance snapshots</strong><br /><span className="text-muted-foreground">Only future measurements from published Motionly exports are retained for {retentionDays} days.</span></span>
          </label>
          <label className="flex items-start gap-2 text-xs">
            <input checked={suggestionsEnabled} disabled={busy || !metricsEnabled} onChange={(event) => setSuggestionsEnabled(event.target.checked)} type="checkbox" />
            <span><strong>Use those snapshots for edit suggestions</strong><br /><span className="text-muted-foreground">Suggestions are observational, workspace-account scoped, and never train a shared model.</span></span>
          </label>
          <label className="block text-xs font-bold">
            Retention period
            <select className="ml-2 rounded border border-foreground bg-background px-2 py-1 text-xs" disabled={busy} onChange={(event) => setRetentionDays(Number(event.target.value))} value={retentionDays}>
              {[30, 60, 90, 180, 365].map((days) => <option key={days} value={days}>{days} days</option>)}
            </select>
          </label>
          <label className="flex items-start gap-2 rounded border border-primary/50 bg-primary/5 p-2 text-[11px]">
            <input checked={acknowledged} disabled={busy} onChange={(event) => setAcknowledged(event.target.checked)} type="checkbox" />
            <span>I acknowledge policy {status.policy_version} and the aggregate-only data boundary.</span>
          </label>
          <Button disabled={busy || !acknowledged} onClick={() => void saveConsent()} size="sm" type="button">Save performance feedback consent</Button>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">This deployment has performance feedback disabled. No performance snapshots can be stored.</p>
      )}

      {enabled && suggestionsEnabled ? (
        <div className="space-y-2 border-t border-foreground/20 pt-3">
          <p className="text-xs font-bold">Editing suggestions</p>
          {insights?.status === "ready" ? insights.suggestions.map((insight) => (
            <div key={insight.kind} className="rounded border border-foreground/30 p-2 text-xs">
              <p>{insight.editor_instruction}</p>
              <p className="mt-1 text-[10px] text-muted-foreground">{insight.confidence} confidence · {insight.sample_count} consented posts · observational only</p>
            </div>
          )) : (
            <p className="text-xs text-muted-foreground">Collecting aggregate snapshots. Suggestions appear after {insights?.minimum_examples ?? status?.minimum_examples ?? 3} consented published posts for this platform.</p>
          )}
        </div>
      ) : null}

      {enabled ? (
        <div className="space-y-2 border-t border-danger/40 pt-3">
          <label className="flex items-start gap-2 text-[11px]">
            <input checked={revokeConfirmed} disabled={busy} onChange={(event) => setRevokeConfirmed(event.target.checked)} type="checkbox" />
            <span>Delete stored performance snapshots and withdraw this consent.</span>
          </label>
          <Button disabled={busy || !revokeConfirmed} onClick={() => void revoke()} size="sm" type="button" variant="danger"><ShieldCheck className="size-3.5" /> Withdraw and delete</Button>
        </div>
      ) : null}
      {message ? <p aria-live="polite" className="text-xs font-semibold">{message}</p> : null}
    </section>
  );
}
