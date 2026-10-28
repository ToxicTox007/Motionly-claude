"use client";

import { fetchWithSession, type ApiAuth } from "@/lib/api-session.mjs";
import { normalizeQueuedLayerStatusUrl } from "@/lib/queued-status-url.mjs";
import { createClient } from "@/lib/supabase/browser";

/**
 * fetch() for the API: authenticated with the browser session's current
 * token, renewed and retried once if the API rejects it.
 */
export function apiFetch(
  input: string,
  init: RequestInit,
  { workspaceId, json = false }: ApiAuth,
): Promise<Response> {
  return fetchWithSession(createClient().auth, input, init, {
    workspaceId,
    json,
  });
}

type EditorApiRequestOptions = {
  workspaceId: string;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: Record<string, unknown>;
  friendlyError?: string;
  json?: boolean;
  signal?: AbortSignal;
};

export class EditorApiError extends Error {
  status: number;
  code?: string;
  requestId?: string;
  technicalMessage?: string;

  constructor({
    message,
    status,
    code,
    requestId,
    technicalMessage,
  }: {
    message: string;
    status: number;
    code?: string;
    requestId?: string;
    technicalMessage?: string;
  }) {
    super(message);
    this.name = "EditorApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.technicalMessage = technicalMessage;
  }
}

export class RevisionConflictError extends EditorApiError {
  expectedRevision: number;
  currentRevision: number;

  constructor({
    expectedRevision,
    currentRevision,
    requestId,
  }: {
    expectedRevision: number;
    currentRevision: number;
    requestId?: string;
  }) {
    super({
      message:
        "Your changes conflict with a newer version. Please reload or force save.",
      status: 409,
      code: "collaboration_revision_conflict",
      requestId,
      technicalMessage: `Expected revision ${expectedRevision}, server is at ${currentRevision}`,
    });
    this.name = "RevisionConflictError";
    this.expectedRevision = expectedRevision;
    this.currentRevision = currentRevision;
  }
}

export async function editorApiRequest<T>(
  path: string,
  {
    workspaceId,
    method = "GET",
    body,
    friendlyError = "That action could not be completed. Please try again.",
    json = true,
    signal,
  }: EditorApiRequestOptions,
): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(
      path,
      {
        method,
        body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
        cache: method === "GET" ? "no-store" : undefined,
        signal,
      },
      { workspaceId, json: json && method !== "GET" },
    );
  } catch (err: any) {
    if (err.name === "SessionExpiredError") {
      throw new EditorApiError({
        message: err.message,
        status: 401,
        code: "auth_session_expired",
        technicalMessage: err.message,
      });
    }
    if (err.name === "AbortError" || err.message?.includes("aborted")) {
      throw new EditorApiError({
        message: "The request was cancelled.",
        status: 499,
        code: "request_aborted",
        technicalMessage: err.message,
      });
    }
    throw new EditorApiError({
      message:
        "Network connection lost. Please verify your internet connection.",
      status: 0,
      code: "network_failure",
      technicalMessage: err.message,
    });
  }

  const payload = (await readJson(response)) as Record<string, unknown> | null;

  if (!response.ok) {
    const error = payload?.error as
      | { code?: string; message?: string; request_id?: string }
      | undefined;

    // Handle 409 revision conflict specifically. The API normalizes every
    // plan conflict to the standard envelope with code
    // "collaboration_revision_conflict"; the flat legacy string is still
    // accepted for forward/backward compatibility during rollout.
    const isRevisionConflict =
      response.status === 409 &&
      (error?.code === "collaboration_revision_conflict" ||
        (payload as any)?.error === "revision_conflict");
    if (isRevisionConflict) {
      throw new RevisionConflictError({
        expectedRevision: (payload as any)?.expected_revision ?? 0,
        currentRevision: (payload as any)?.current_revision ?? 0,
        requestId: error?.request_id,
      });
    }

    const technicalMessage = error?.message ?? response.statusText;
    const shouldHideTechnical =
      error?.code === "request_validation_failed" ||
      /valid dictionary|extract fields|validation/i.test(technicalMessage);
    const message = shouldHideTechnical
      ? friendlyError
      : technicalMessage || friendlyError;
    const apiError = new EditorApiError({
      message,
      status: response.status,
      code: error?.code,
      requestId: error?.request_id,
      technicalMessage,
    });
    console.error("Editor API request failed", {
      path,
      method,
      status: response.status,
      body,
      payload,
    });
    throw apiError;
  }

  return payload as T;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { error: { message: text } };
  }
}

export type PlanJobStatusResponse = {
  status: "queued" | "running" | "succeeded" | "awaiting_approval" | "failed";
  progress: number;
  plan_id?: string;
  proposal_id?: string;
  proposal_revision?: number;
  failure_reason?: string;
};

export type PlanJobIdentity =
  | { status: "succeeded"; planId: string }
  | {
      status: "awaiting_approval";
      proposalId: string;
      proposalRevision: number;
    };

export function pollPlanJob(
  statusUrl: string,
  options: EditorApiRequestOptions,
  onProgress: ((progress: number) => void) | undefined,
  returnIdentity: true,
): Promise<PlanJobIdentity>;
export function pollPlanJob(
  statusUrl: string,
  options: EditorApiRequestOptions,
  onProgress?: (progress: number) => void,
): Promise<string>;
export async function pollPlanJob(
  statusUrl: string,
  options: EditorApiRequestOptions,
  onProgress?: (progress: number) => void,
  returnIdentity = false,
): Promise<string | PlanJobIdentity> {
  const maxRetries = 120;
  for (let i = 0; i < maxRetries; i++) {
    await waitForInteractivePollingTurn(options.signal);
    const response = await editorApiRequest<PlanJobStatusResponse>(statusUrl, {
      ...options,
      method: "GET",
      body: undefined,
    });
    if (response.status === "awaiting_approval") {
      if (!response.proposal_id || response.proposal_revision == null) {
        throw new Error(
          "Plan proposal is ready but its approval identity is missing.",
        );
      }
      if (!returnIdentity) {
        throw new Error(
          "This plan requires approval before it can be applied.",
        );
      }
      return {
        status: "awaiting_approval",
        proposalId: response.proposal_id,
        proposalRevision: response.proposal_revision,
      };
    }
    if (response.status === "succeeded") {
      if (!response.plan_id) {
        throw new Error("Job succeeded but returned no plan ID.");
      }
      return returnIdentity
        ? { status: "succeeded", planId: response.plan_id }
        : response.plan_id;
    }
    if (response.status === "failed") {
      throw new Error(response.failure_reason || "Plan generation failed.");
    }
    if (onProgress && response.progress !== undefined) {
      onProgress(response.progress);
    }
    await delay(Math.min(3000, 1000 + i * 100), options.signal);
  }
  throw new Error("Plan generation timed out.");
}

type AdaptivePollingOptions = {
  intervalMs?: number;
  maxIntervalMs?: number;
  backoffStepMs?: number;
  immediate?: boolean;
};

export type LayerJobStatusResponse = {
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  failure_reason?: string;
};

export async function pollLayerJob(
  statusUrl: string,
  options: EditorApiRequestOptions,
  onProgress?: (progress: number) => void,
): Promise<void> {
  const maxRetries = 120;
  for (let attempt = 0; attempt < maxRetries; attempt += 1) {
    await waitForInteractivePollingTurn(options.signal);
    const response = await editorApiRequest<LayerJobStatusResponse>(statusUrl, {
      ...options,
      method: "GET",
      body: undefined,
    });
    if (response.status === "succeeded") return;
    if (response.status === "failed") {
      throw new Error(
        response.failure_reason || "Background processing failed.",
      );
    }
    onProgress?.(response.progress);
    await delay(Math.min(3000, 1000 + attempt * 100), options.signal);
  }
  throw new Error("Background processing timed out.");
}

export type QueuedLayerSubmission = {
  status: "ready" | "queued";
  status_url?: string | null;
};

export async function resolveQueuedLayer<T>({
  submission,
  immediate,
  apiBaseUrl,
  options,
  load,
  resourceLabel,
  onProgress,
}: {
  submission: QueuedLayerSubmission;
  immediate?: T | null;
  apiBaseUrl: string;
  options: EditorApiRequestOptions;
  load: () => Promise<T | null | undefined>;
  resourceLabel: string;
  onProgress?: (progress: number) => void;
}): Promise<T> {
  if (submission.status === "queued") {
    if (!submission.status_url) {
      throw new Error(`${resourceLabel} was queued without a status URL.`);
    }
    const statusUrl = normalizeQueuedLayerStatusUrl(
      submission.status_url,
      apiBaseUrl,
    );
    await pollLayerJob(statusUrl, options, onProgress);
  }
  const resource = immediate ?? (await load());
  if (resource == null) {
    throw new Error(`${resourceLabel} completed but could not be loaded.`);
  }
  return resource;
}

export function startAdaptivePolling(
  callback: (signal: AbortSignal) => void | Promise<void>,
  {
    intervalMs = 3000,
    maxIntervalMs = 10_000,
    backoffStepMs = 500,
    immediate = true,
  }: AdaptivePollingOptions = {},
): () => void {
  let stopped = false;
  let timer: number | null = null;
  let turn = 0;
  const controller = new AbortController();

  const clear = () => {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  };
  const schedule = (delayMs: number) => {
    clear();
    timer = window.setTimeout(() => {
      void tick();
    }, delayMs);
  };
  const tick = async () => {
    if (stopped) return;
    let succeeded = false;
    try {
      await waitForInteractivePollingTurn(controller.signal);
      if (stopped) return;
      await callback(controller.signal);
      succeeded = true;
    } catch (error) {
      if (!controller.signal.aborted) {
        console.warn("Adaptive polling turn failed; retrying.", error);
      }
    } finally {
      if (!stopped) {
        // Back off only across consecutive failures; a successful turn
        // returns to the base interval.
        if (succeeded) turn = 0;
        const nextDelay = Math.min(
          maxIntervalMs,
          intervalMs + turn * backoffStepMs,
        );
        if (!succeeded) turn += 1;
        schedule(nextDelay);
      }
    }
  };

  if (immediate) {
    void tick();
  } else {
    schedule(intervalMs);
  }

  return () => {
    stopped = true;
    controller.abort();
    clear();
  };
}

export async function waitForInteractivePollingTurn(
  signal?: AbortSignal,
): Promise<void> {
  throwIfAborted(signal);
  if (
    typeof document !== "undefined" &&
    document.visibilityState !== "visible"
  ) {
    await waitForBrowserEvent(
      document,
      "visibilitychange",
      () => document.visibilityState === "visible",
      signal,
    );
  }
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    await waitForBrowserEvent(window, "online", () => navigator.onLine, signal);
  }
  throwIfAborted(signal);
}

function waitForBrowserEvent(
  target: EventTarget,
  eventName: string,
  isReady: () => boolean,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      target.removeEventListener(eventName, onEvent);
      signal?.removeEventListener("abort", onAbort);
    };
    const onEvent = () => {
      if (!isReady()) return;
      cleanup();
      resolve();
    };
    const onAbort = () => {
      cleanup();
      reject(new DOMException("The operation was aborted.", "AbortError"));
    };
    target.addEventListener(eventName, onEvent);
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) onAbort();
  });
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  throwIfAborted(signal);
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
}
