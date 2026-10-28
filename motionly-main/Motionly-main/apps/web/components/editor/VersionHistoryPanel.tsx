"use client";

import React, { useEffect, useState } from "react";
import { History, Loader2, RotateCcw, Save } from "lucide-react";
import { Button } from "@/components/button";
import { Modal } from "@/components/brutal-ui";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import type {
  EditPlanRecordSummary,
  PlanSnapshotSummary,
} from "@motionly/shared";

interface VersionHistoryPanelProps {
  projectId: string;
  workspaceId: string;
  activePlan: EditPlanRecordSummary | null;
  onPlanRestored: (plan: EditPlanRecordSummary) => void;
}

export function VersionHistoryPanel({
  projectId,
  workspaceId,
  activePlan,
  onPlanRestored,
}: VersionHistoryPanelProps) {
  const [snapshots, setSnapshots] = useState<PlanSnapshotSummary[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [pendingRestore, setPendingRestore] =
    useState<PlanSnapshotSummary | null>(null);

  useEffect(() => {
    let canceled = false;
    void editorApiRequest<{ snapshots: PlanSnapshotSummary[] }>(
      `${env.apiBaseUrl}/projects/${projectId}/plans/snapshots`,
      {
        workspaceId,
        friendlyError: "Version history could not be loaded.",
      },
    )
      .then((payload) => {
        if (!canceled) setSnapshots(payload.snapshots);
      })
      .catch((error) => {
        if (!canceled) setMessage((error as Error).message);
      });
    return () => {
      canceled = true;
    };
  }, [projectId, workspaceId]);

  async function saveSnapshot() {
    if (!activePlan) return;
    setBusy(true);
    try {
      const payload = await editorApiRequest<{ snapshot: PlanSnapshotSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/snapshots`,
        {
          method: "POST",
          workspaceId,
          friendlyError: "Snapshot could not be saved.",
          body: {
            plan_id: activePlan.id,
            name: name.trim() || `Version ${snapshots.length + 1}`,
          },
        },
      );
      setSnapshots((current) => [payload.snapshot, ...current]);
      setName("");
      setMessage("Named version saved.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function restore(snapshot: PlanSnapshotSummary) {
    setPendingRestore(null);
    setBusy(true);
    try {
      const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/snapshots/${snapshot.id}/restore`,
        {
          method: "POST",
          workspaceId,
          friendlyError: "Version could not be restored.",
          body: {},
        },
      );
      onPlanRestored(payload.plan);
      setMessage(`Restored ${snapshot.name}.`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const isErrorMessage = message && /could not|failed/i.test(message);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-bold tracking-tight text-foreground">
          Version history
        </h2>
        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
          Save named, restorable copies of the complete edit manifest.
        </p>
      </div>

      {/* Save input row */}
      <div className="flex gap-2.5">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="e.g. Client review"
          className="min-h-[40px] min-w-0 flex-1 rounded-neu border border-control bg-surface px-3 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
        />
        <button
          type="button"
          disabled={busy || !activePlan}
          onClick={() => void saveSnapshot()}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-neu bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 focus-visible:outline-none active:shadow-neu-pressed disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-neu-raised-sm focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          {busy ? (
            <Loader2 className="size-4 animate-spin" strokeWidth={1.75} />
          ) : (
            <Save className="size-4" strokeWidth={1.75} />
          )}
          <span>Save</span>
        </button>
      </div>

      {/* Snapshot list */}
      <div className="space-y-2">
        {snapshots.length > 0 ? (
          <>
            <p className="mb-2 mt-4 px-1 text-xs font-semibold text-foreground">
              Saved versions
            </p>
            {snapshots.map((snapshot) => (
              <div
                key={snapshot.id}
                className="flex items-center justify-between gap-3 rounded-neu-md bg-surface p-3 shadow-neu-raised-sm"
              >
                <div className="flex min-w-0 items-center gap-2.5">
                  <History
                    className="size-4 shrink-0 text-primary"
                    strokeWidth={1.75}
                  />
                  <div className="min-w-0">
                    <p className="truncate text-xs font-semibold text-foreground">
                      {snapshot.name}
                    </p>
                    <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                      Plan v{snapshot.source_plan_version} ·{" "}
                      {new Date(snapshot.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setPendingRestore(snapshot)}
                  className="neu-pressable flex h-8 items-center gap-1 whitespace-nowrap rounded-neu bg-surface px-3 text-xs font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised disabled:opacity-50 disabled:shadow-neu-raised-sm"
                >
                  <RotateCcw className="size-3" strokeWidth={1.75} />
                  <span>Restore</span>
                </button>
              </div>
            ))}
          </>
        ) : (
          <p className="rounded-neu-md bg-surface p-5 text-center text-xs font-medium leading-relaxed text-muted-foreground shadow-neu-inset-sm">
            No named versions yet.
          </p>
        )}
      </div>

      {/* Status / error */}
      {message ? (
        <p
          role={isErrorMessage ? "alert" : "status"}
          aria-live="polite"
          className={
            isErrorMessage
              ? "rounded-neu-md bg-surface p-3.5 text-xs font-semibold text-danger shadow-neu-raised-sm ring-1 ring-danger/30"
              : "rounded-neu-md bg-surface p-3.5 text-xs font-semibold text-foreground shadow-neu-raised-sm"
          }
        >
          {message}
        </p>
      ) : null}
      <Modal
        open={Boolean(pendingRestore)}
        title="Restore version"
        description={
          pendingRestore
            ? `${pendingRestore.name} will replace the current edit plan.`
            : undefined
        }
        onClose={() => setPendingRestore(null)}
        closeDisabled={busy}
        footer={
          <>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setPendingRestore(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={!pendingRestore || busy}
              onClick={() => pendingRestore && void restore(pendingRestore)}
            >
              Restore
            </Button>
          </>
        }
      >
        <p className="text-sm font-bold text-muted-foreground">
          Current unsaved plan changes will be replaced.
        </p>
      </Modal>
    </div>
  );
}
