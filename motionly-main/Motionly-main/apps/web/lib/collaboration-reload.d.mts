export const MANIFEST_UPDATED: "manifest_updated";
export const PLAN_REVISION_UPDATED: "plan_revision_updated";
export const PANEL_COLLABORATION: "collaboration";
export const PANEL_PLAN: "plan";

export interface CollaborationBroadcast {
  event: string;
  payload?: {
    project_id?: string;
    workspace_id?: string;
    plan_id?: string;
    revision?: number;
  };
}

export interface PanelReload {
  panel: string;
  revision: number | null;
  planId: string | null;
}

export function panelReloadForBroadcast(
  message: CollaborationBroadcast | null | undefined,
): PanelReload | null;

export function shouldApplyRevision(
  incomingRevision: number | null | undefined,
  trackedRevision: number | null | undefined,
): boolean;
