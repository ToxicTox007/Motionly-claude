"use client";

import React, { useEffect, useState } from "react";
import { History, Loader2, RotateCcw, Save } from "lucide-react";
import { Button } from "@/components/button";
import { Modal } from "@/components/brutal-ui";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import { useFocusTrap } from "@/lib/useFocusTrap";
import type { EditPlanRecordSummary, PlanSnapshotSummary } from "@motionly/shared";

interface VersionHistoryPanelProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  activePlan: EditPlanRecordSummary | null;
  onPlanRestored: (plan: EditPlanRecordSummary) => void;
}

export function VersionHistoryPanel({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  activePlan,
  onPlanRestored,
}: VersionHistoryPanelProps) {
  const [snapshots, setSnapshots] = useState<PlanSnapshotSummary[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [pendingRestore, setPendingRestore] =
    useState<PlanSnapshotSummary | null>(null);
  const restoreModalRef = useFocusTrap(Boolean(pendingRestore), () =>
    setPendingRestore(null),
  );

  useEffect(() => {
    let canceled = false;
    void editorApiRequest<{ snapshots: PlanSnapshotSummary[] }>(
      `${env.apiBaseUrl}/projects/${projectId}/plans/snapshots`,
      {
        workspaceId,
        userId,
        accessToken: apiAccessToken,
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
  }, [apiAccessToken, projectId, userId, workspaceId]);

  async function saveSnapshot() {
    if (!activePlan) return;
    setBusy(true);
    try {
      const payload = await editorApiRequest<{ snapshot: PlanSnapshotSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/snapshots`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
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
          userId,
          accessToken: apiAccessToken,
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
        <h2 className="text-lg font-black uppercase tracking-tight">Version history</h2>
        <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
          Save named, restorable copies of the complete edit manifest.
        </p>
      </div>

      {/* Save input row */}
      <div className="flex gap-2.5">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="e.g. Client review"
          className="min-h-[40px] min-w-0 flex-1 px-3 py-2 text-xs font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] dark:focus:shadow-[2px_2px_0px_#fff] transition-all"
        />
        <button
          type="button"
          disabled={busy || !activePlan}
          onClick={() => void saveSnapshot()}
          className="inline-flex items-center justify-center gap-2 h-10 rounded border-2 border-foreground bg-primary text-primary-foreground text-xs font-bold hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] dark:hover:shadow-[2px_2px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none px-4 focus:outline-none transition-all duration-[100ms]"
        >
          {busy ? (
            <Loader2 className="size-4 animate-spin" strokeWidth={2} />
          ) : (
            <Save className="size-4" strokeWidth={2} />
          )}
          <span>Save</span>
        </button>
      </div>

      {/* Snapshot list */}
      <div className="space-y-2">
        {snapshots.length > 0 ? (
          <>
            <p className="text-[10px] font-black uppercase tracking-wider text-foreground font-mono mt-4 mb-2 px-1">
              Saved versions
            </p>
            {snapshots.map((snapshot) => (
              <div
                key={snapshot.id}
                className="card rounded border-2 border-foreground p-3 flex items-center justify-between gap-3 bg-surface shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <History className="size-4 shrink-0 text-primary" strokeWidth={2} />
                  <div className="min-w-0">
                    <p className="truncate text-xs font-bold text-foreground">{snapshot.name}</p>
                    <p className="text-[10px] text-muted-foreground font-mono mt-0.5">
                      Plan v{snapshot.source_plan_version} ·{" "}
                      {new Date(snapshot.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setPendingRestore(snapshot)}
                  className="flex h-8 items-center gap-1 rounded border-2 border-foreground px-3 text-xs font-bold hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[1px_1px_0px_#000] dark:hover:shadow-[1px_1px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none disabled:transform-none disabled:shadow-none bg-surface text-foreground transition-all duration-[100ms] whitespace-nowrap"
                >
                  <RotateCcw className="size-3" strokeWidth={2} />
                  <span>Restore</span>
                </button>
              </div>
            ))}
          </>
        ) : (
          <p className="rounded border-2 border-dashed border-foreground p-5 text-center text-xs font-mono font-bold text-muted-foreground leading-relaxed">
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
              ? "rounded border-2 border-danger bg-danger/10 p-3.5 text-xs font-bold text-danger shadow-[2px_2px_0px_#000]"
              : "rounded border-2 border-foreground bg-muted p-3.5 text-xs font-bold text-foreground font-mono shadow-[2px_2px_0px_#000]"
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
        contentRef={restoreModalRef}
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
