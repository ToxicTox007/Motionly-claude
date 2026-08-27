"use client";

import { BrainCircuit, RefreshCw, ShieldCheck } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/button";
import { EditorApiError, editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";

type EditorialLearningStatus = {
  enabled: boolean;
  policy_version: string;
  default_retention_days: number;
  minimum_examples: number;
};

type EditorialLearningPolicy = {
  memory_enabled: boolean;
  training_enabled: boolean;
  policy_version: string;
  revision: number;
  retention_days: number;
  revoked_at?: string | null;
  updated_at?: string | null;
};

export function EditorialLearningSettings({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const [status, setStatus] = useState<EditorialLearningStatus | null>(null);
  const [policy, setPolicy] = useState<EditorialLearningPolicy | null>(null);
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [trainingEnabled, setTrainingEnabled] = useState(false);
  const [retentionDays, setRetentionDays] = useState(90);
  const [acknowledged, setAcknowledged] = useState(false);
  const [withdrawConfirmed, setWithdrawConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const requestOptions = useMemo(
    () => ({
      workspaceId,
      friendlyError: "Editorial learning settings could not be loaded.",
    }),
    [workspaceId],
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      const [nextStatus, nextPolicy] = await Promise.all([
        editorApiRequest<EditorialLearningStatus>(
          `${env.apiBaseUrl}/editorial-learning/status`,
          requestOptions,
        ),
        editorApiRequest<EditorialLearningPolicy>(
          `${env.apiBaseUrl}/editorial-learning/policy`,
          requestOptions,
        ),
      ]);
      setStatus(nextStatus);
      setPolicy(nextPolicy);
      setMemoryEnabled(nextPolicy.memory_enabled);
      setTrainingEnabled(nextPolicy.training_enabled);
      setRetentionDays(
        nextPolicy.retention_days || nextStatus.default_retention_days,
      );
    } catch (requestError) {
      setError((requestError as Error).message);
    }
  }, [requestOptions]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!status?.enabled) return;
    if (!acknowledged) {
      setError("Confirm that you understand the consent scope before saving.");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const nextPolicy = await editorApiRequest<EditorialLearningPolicy>(
        `${env.apiBaseUrl}/editorial-learning/policy`,
        {
          ...requestOptions,
          method: "PUT",
          body: {
            memory_enabled: memoryEnabled,
            training_enabled: trainingEnabled,
            retention_days: retentionDays,
            policy_version: status.policy_version,
            expected_revision: policy?.revision ?? 0,
          },
          friendlyError:
            "Editorial learning settings could not be saved. Reload and try again.",
        },
      );
      setPolicy(nextPolicy);
      setAcknowledged(false);
      setMessage(
        memoryEnabled || trainingEnabled
          ? "Your editorial-learning consent has been saved."
          : "Editorial learning remains disabled for your account.",
      );
    } catch (requestError) {
      if (
        requestError instanceof EditorApiError &&
        requestError.status === 409
      ) {
        setError(
          "Your consent settings changed elsewhere. Reload before saving.",
        );
      } else {
        setError((requestError as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function withdrawAllConsent() {
    if (!withdrawConfirmed) {
      setError("Confirm the withdrawal before continuing.");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const nextPolicy = await editorApiRequest<EditorialLearningPolicy>(
        `${env.apiBaseUrl}/editorial-learning/revoke`,
        {
          ...requestOptions,
          method: "POST",
          body: {
            memory: true,
            training: true,
            reason: "account_settings_withdrawal",
          },
          friendlyError: "Consent could not be withdrawn. Please try again.",
        },
      );
      setPolicy(nextPolicy);
      setMemoryEnabled(false);
      setTrainingEnabled(false);
      setWithdrawConfirmed(false);
      setMessage(
        "Consent was withdrawn. Eligible learning data and active preference use were revoked.",
      );
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const policyState = policy?.revoked_at
    ? "withdrawn"
    : policy?.memory_enabled || policy?.training_enabled
      ? "enabled"
      : "off";

  return (
    <section
      className="card rounded-xl p-6 space-y-5 max-w-4xl"
      aria-labelledby="editorial-learning-heading"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <BrainCircuit className="size-5 text-primary" aria-hidden="true" />
            <h3 id="editorial-learning-heading" className="text-lg font-black">
              Editorial learning
            </h3>
          </div>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Choose whether Motionly can retain normalized edit preferences for
            your workspace. Source video, transcript text, prompts, URLs, and
            raw editor content are not used by this setting.
          </p>
        </div>
        <span
          className={`badge shrink-0 ${status?.enabled ? "badge-primary" : "badge-secondary"}`}
        >
          {status
            ? status.enabled
              ? `Feature ${policyState}`
              : "Feature unavailable"
            : "Loading"}
        </span>
      </div>

      {status?.enabled === false ? (
        <div className="rounded-lg border border-border bg-muted p-4 text-sm text-muted-foreground">
          Editorial learning is disabled for this deployment. No new learning
          preferences can be enabled here.
        </div>
      ) : null}

      {status?.enabled ? (
        <form className="space-y-5" onSubmit={savePolicy}>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="rounded-lg border border-border bg-muted p-4">
              <span className="flex items-start gap-3">
                <input
                  checked={memoryEnabled}
                  className="mt-1 size-4"
                  disabled={busy}
                  onChange={(event) => setMemoryEnabled(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  <span className="block text-sm font-bold">
                    Remember structured preferences
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    Allow a reviewed, promoted dataset to influence future plan
                    suggestions for your projects.
                  </span>
                </span>
              </span>
            </label>
            <label className="rounded-lg border border-border bg-muted p-4">
              <span className="flex items-start gap-3">
                <input
                  checked={trainingEnabled}
                  className="mt-1 size-4"
                  disabled={busy}
                  onChange={(event) => setTrainingEnabled(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  <span className="block text-sm font-bold">
                    Create training datasets
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    Permit normalized kept, changed, or rejected edit signals to
                    be frozen for an explicit training run.
                  </span>
                </span>
              </span>
            </label>
          </div>

          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
            <label>
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Retention period
              </span>
              <select
                className="pixel-input h-11 w-full px-3"
                disabled={busy}
                onChange={(event) =>
                  setRetentionDays(Number(event.target.value))
                }
                value={retentionDays}
              >
                {[30, 60, 90, 180, 365].map((days) => (
                  <option key={days} value={days}>
                    {days} days
                  </option>
                ))}
              </select>
            </label>
            <p className="text-xs text-muted-foreground">
              Training requires at least{" "}
              <strong>{status.minimum_examples}</strong> consented edit signals
              per project.
            </p>
          </div>

          <label className="flex items-start gap-3 rounded-lg border border-primary/40 bg-primary/5 p-4 text-sm">
            <input
              checked={acknowledged}
              className="mt-1 size-4"
              disabled={busy}
              onChange={(event) => setAcknowledged(event.target.checked)}
              type="checkbox"
            />
            <span>
              <span className="font-bold">
                I acknowledge policy {status.policy_version}.
              </span>
              <span className="mt-1 block text-xs text-muted-foreground">
                Consent is scoped to this workspace and account. It is off by
                default and can be withdrawn below.
              </span>
            </span>
          </label>
          <div className="flex flex-wrap gap-3">
            <Button disabled={busy || !acknowledged} type="submit">
              Save consent choices
            </Button>
            <Button
              disabled={busy}
              onClick={() => void load()}
              type="button"
              variant="secondary"
            >
              <RefreshCw className="size-4" aria-hidden="true" /> Reload
              settings
            </Button>
          </div>
        </form>
      ) : null}

      {status?.enabled &&
      (policy?.memory_enabled || policy?.training_enabled) ? (
        <div className="rounded-lg border border-danger/50 bg-danger/5 p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-bold">
            <ShieldCheck className="size-4" aria-hidden="true" /> Withdraw all
            editorial-learning consent
          </div>
          <p className="text-xs text-muted-foreground">
            This immediately makes your eligible deltas and frozen datasets
            unavailable for learning and withdraws active preference use for
            this workspace account.
          </p>
          <label className="flex items-start gap-2 text-xs">
            <input
              checked={withdrawConfirmed}
              disabled={busy}
              onChange={(event) => setWithdrawConfirmed(event.target.checked)}
              type="checkbox"
            />
            I understand that this revokes both memory and training consent.
          </label>
          <Button
            disabled={busy || !withdrawConfirmed}
            onClick={() => void withdrawAllConsent()}
            type="button"
            variant="danger"
          >
            Withdraw all consent
          </Button>
        </div>
      ) : null}

      {message ? (
        <p aria-live="polite" className="text-sm font-semibold text-success">
          {message}
        </p>
      ) : null}
      {error ? (
        <p aria-live="assertive" className="text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
    </section>
  );
}
