"use client";

// Task 32 — user model choice state for the UI (plan Section 2.11).
//
// The operator supplies the models; a user chooses among the operator's
// selectable routes per capability. A choice selects a route — it never adds
// a model, a key or a serving location (invariant 12). This module fetches the
// authenticated `GET /model-options` contract through the existing API client
// and the workspace/project settings routes, and turns them into a small,
// reusable state the pickers render.
//
// Like `lib/capabilities.ts`, the client state here is a strict subset of the
// contract: it carries display data only (a display name and description the
// operator may set, closed-set labels, a quota weight, a status and the
// per-workspace allowance with a reason). It holds no serving location, no
// model identity and no credential, and nothing here reintroduces one — the
// picker shows operator display text and the option id only, never a vendor
// model identifier (the option id is the registry route id, an opaque token).

import type { ApiResponse, components } from "@motionly/shared";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";

// Route paths. Kept as literals here so the one module that talks to the
// model-choice API is the single place the paths live (and the dead-code
// route-caller scanner sees them called from product source).
export const MODEL_OPTIONS_PATH = "/model-options";
export const WORKSPACE_MODEL_SETTINGS_PATH =
  "/workspaces/current/model-settings";
export function projectModelSettingsPath(projectId: string): string {
  return `/projects/${projectId}/model-settings`;
}

/** The typed `GET /model-options` response from the generated contract. */
export type ModelOptionsResponse = ApiResponse<"GET /model-options">;
/** One capability's options block. */
export type CapabilityOptions = ModelOptionsResponse["capabilities"][number];
/** One selectable option (option id is the registry route id, an opaque token). */
export type ModelOption = CapabilityOptions["options"][number];

/** The workspace setting update body (owner/admin only). */
export type WorkspaceModelSettingUpdate =
  components["schemas"]["WorkspaceModelSettingUpdate"];
/** The project setting update body (any member who can edit the project). */
export type ProjectModelSettingUpdate =
  components["schemas"]["ProjectModelSettingUpdate"];
/** The per-capability workspace setting items. */
export type WorkspaceModelSettings =
  ApiResponse<"GET /workspaces/current/model-settings">["settings"];

// The closed label set (plan Section 2.11, Task 29). Visible, neutral words —
// they carry no vendor identity. Unknown labels are shown verbatim but the
// known ones get stable human copy.
const LABEL_TEXT: Record<string, string> = {
  fast: "Fast",
  balanced: "Balanced",
  quality: "Quality",
  economy: "Economy",
  local: "On-device",
};

/** Human copy for a label token, falling back to the token itself. */
export function labelText(label: string): string {
  return LABEL_TEXT[label] ?? label;
}

/**
 * The quota-weight line shown beside an option. A weight of 1 is the baseline
 * and shows nothing; other weights read as "uses 2× quota" (plan Task 32). The
 * state is carried by text, never colour alone.
 */
export function quotaWeightText(weight: number): string | null {
  if (!Number.isFinite(weight) || weight === 1) return null;
  // Trim a trailing ".0" so 2 reads "2×", not "2.0×".
  const rounded = Math.round(weight * 10) / 10;
  const display = Number.isInteger(rounded) ? String(rounded) : String(rounded);
  return `uses ${display}× quota`;
}

/**
 * Why an option may not be picked by this workspace, as operator-facing copy.
 * `required_plan` turns the generic reason into "Requires <plan>" so the user
 * learns the upgrade path (plan Task 32). Returns null when the option is
 * allowed. Never names a vendor or a model id.
 */
export function optionDisabledReason(option: ModelOption): string | null {
  if (option.allowed) return null;
  if (option.required_plan) {
    const plan = option.required_plan;
    const pretty = plan.charAt(0).toUpperCase() + plan.slice(1);
    return `Requires ${pretty}`;
  }
  switch (option.reason) {
    case "plan_not_allowed":
      return "Not available on your plan";
    case "option_unavailable":
      return "Not configured by your operator";
    case "workspace_restricted":
      return "Not allowed in this workspace";
    default:
      return "Unavailable";
  }
}

/** True when the option is a currently usable choice for this workspace. */
export function optionSelectable(option: ModelOption): boolean {
  return option.allowed && option.status === "available";
}

// ---------------------------------------------------------------------------
// Fallback notice shape (Task 31 adds `model_choice` to AI-result responses).
// Declared here as a local type so the notice component does not depend on a
// generated type that may not exist yet in this working tree.
// ---------------------------------------------------------------------------

/** The optional `model_choice` block an AI-result response may carry. */
export type ModelChoice = {
  requested_option_id?: string | null;
  served_option_id?: string | null;
  choice_source?: string | null;
  fallback_reason?: string | null;
};

/**
 * The human sentence shown when a fallback happened, or null when the served
 * route is the requested one. Neutral copy: it names neither the requested nor
 * the served route's model id, only that a different model answered. The UI
 * tells the user when a fallback happened (plan Section 2.11).
 */
export function fallbackNoticeText(
  choice: ModelChoice | null | undefined,
): string | null {
  if (!choice) return null;
  if (!choice.fallback_reason) return null;
  return "Your chosen model was unavailable, so another model completed this. The result is still yours.";
}

// ---------------------------------------------------------------------------
// Catalogue state
// ---------------------------------------------------------------------------

export type ModelOptionsCatalog = {
  capabilities: CapabilityOptions[];
  /** Neutral: the options could not be fetched; the picker shows an unknown state. */
  statusUnknown: boolean;
};

/** The neutral catalogue used when the API cannot be reached. */
export function unknownOptionsCatalog(): ModelOptionsCatalog {
  return { capabilities: [], statusUnknown: true };
}

/** Build the catalogue from a fetched response. */
export function catalogFromResponse(
  response: ModelOptionsResponse,
): ModelOptionsCatalog {
  return { capabilities: response.capabilities, statusUnknown: false };
}

/** Find one capability's options in the catalogue, or null. */
export function capabilityOptions(
  catalog: ModelOptionsCatalog,
  capability: string,
): CapabilityOptions | null {
  return (
    catalog.capabilities.find((entry) => entry.capability === capability) ??
    null
  );
}

// ---------------------------------------------------------------------------
// Fetch helpers (never throw — mirror lib/capabilities.ts)
// ---------------------------------------------------------------------------

/**
 * Fetch the model options for the workspace (or a project, when `projectId` is
 * given, so the effective choice reflects the project override). On any failure
 * it returns the neutral unknown catalogue (never throws).
 */
export async function fetchModelOptions({
  workspaceId,
  projectId,
  signal,
}: {
  workspaceId: string;
  projectId?: string;
  signal?: AbortSignal;
}): Promise<ModelOptionsCatalog> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  try {
    const response = await editorApiRequest<ModelOptionsResponse>(
      `${env.apiBaseUrl}${MODEL_OPTIONS_PATH}${query}`,
      {
        workspaceId,
        method: "GET",
        friendlyError: "Model options are unavailable right now.",
        signal,
      },
    );
    return catalogFromResponse(response);
  } catch {
    return unknownOptionsCatalog();
  }
}

/** Fetch the workspace model settings. Returns [] on failure (never throws). */
export async function fetchWorkspaceModelSettings({
  workspaceId,
  signal,
}: {
  workspaceId: string;
  signal?: AbortSignal;
}): Promise<WorkspaceModelSettings> {
  try {
    const response = await editorApiRequest<
      ApiResponse<"GET /workspaces/current/model-settings">
    >(`${env.apiBaseUrl}${WORKSPACE_MODEL_SETTINGS_PATH}`, {
      workspaceId,
      method: "GET",
      friendlyError: "Workspace model settings are unavailable right now.",
      signal,
    });
    return response.settings ?? [];
  } catch {
    return [];
  }
}

/**
 * Save a workspace model setting (owner/admin only; the API enforces the role).
 * Returns the API error code on failure so the UI can show neutral copy.
 */
export async function saveWorkspaceModelSetting({
  workspaceId,
  update,
}: {
  workspaceId: string;
  update: WorkspaceModelSettingUpdate;
}): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    await editorApiRequest(
      `${env.apiBaseUrl}${WORKSPACE_MODEL_SETTINGS_PATH}`,
      {
        workspaceId,
        method: "PUT",
        body: update as unknown as Record<string, unknown>,
        friendlyError: "That model setting could not be saved.",
      },
    );
    return { ok: true };
  } catch (error) {
    return { ok: false, error: errorCode(error) };
  }
}

/**
 * Save a project model override (any member who can edit the project). The API
 * returns 409 `model_choice_locked` when the workspace is locked and 422
 * `model_choice_option_invalid` for a disallowed option; both surface as the
 * error code so the picker shows neutral copy.
 */
export async function saveProjectModelSetting({
  workspaceId,
  projectId,
  update,
}: {
  workspaceId: string;
  projectId: string;
  update: ProjectModelSettingUpdate;
}): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    await editorApiRequest(
      `${env.apiBaseUrl}${projectModelSettingsPath(projectId)}`,
      {
        workspaceId,
        method: "PUT",
        body: update as unknown as Record<string, unknown>,
        friendlyError: "That model choice could not be saved.",
      },
    );
    return { ok: true };
  } catch (error) {
    return { ok: false, error: errorCode(error) };
  }
}

/** Clear a project model override, reverting to the workspace default. */
export async function clearProjectModelSetting({
  workspaceId,
  projectId,
  capability,
}: {
  workspaceId: string;
  projectId: string;
  capability: string;
}): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    await editorApiRequest(
      `${env.apiBaseUrl}${projectModelSettingsPath(projectId)}?capability=${encodeURIComponent(capability)}`,
      {
        workspaceId,
        method: "DELETE",
        friendlyError: "That model choice could not be cleared.",
      },
    );
    return { ok: true };
  } catch (error) {
    return { ok: false, error: errorCode(error) };
  }
}

function errorCode(error: unknown): string {
  const code = (error as { code?: string } | null)?.code;
  return code ?? "model_setting_save_failed";
}
