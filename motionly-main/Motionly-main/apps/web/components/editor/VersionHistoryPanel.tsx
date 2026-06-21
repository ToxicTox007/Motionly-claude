"use client";

import React, { useEffect, useState } from "react";
import { History, Loader2, RotateCcw, Save } from "lucide-react";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
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
    if (!window.confirm(`Restore “${snapshot.name}”? Current unsaved plan changes will be replaced.`)) return;
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

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-black">Version history</h2>
        <p className="text-sm text-muted-foreground">Save named, restorable copies of the complete edit manifest.</p>
      </div>
      <div className="flex gap-2">
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Client review" className="pixel-input min-h-11 min-w-0 flex-1 px-3" />
        <button type="button" disabled={busy || !activePlan} onClick={() => void saveSnapshot()} className="pixel-button flex min-h-11 items-center gap-2 bg-primary px-3 text-sm font-bold text-primary-foreground">
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Save
        </button>
      </div>
      <div className="space-y-2">
        {snapshots.map((snapshot) => (
          <div key={snapshot.id} className="border border-border bg-muted/30 p-3">
            <div className="flex items-start gap-2">
              <History className="mt-0.5 size-4 text-primary" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-bold">{snapshot.name}</p>
                <p className="text-[10px] text-muted-foreground">Plan v{snapshot.source_plan_version} · {new Date(snapshot.created_at).toLocaleString()}</p>
              </div>
              <button type="button" disabled={busy} onClick={() => void restore(snapshot)} className="pixel-button flex min-h-9 items-center gap-1 px-2 text-xs font-bold"><RotateCcw className="size-3" />Restore</button>
            </div>
          </div>
        ))}
        {!snapshots.length ? <p className="border border-dashed border-border p-4 text-center text-sm text-muted-foreground">No named versions yet.</p> : null}
      </div>
      {message ? <p className="text-xs font-bold text-muted-foreground" aria-live="polite">{message}</p> : null}
    </div>
  );
}
