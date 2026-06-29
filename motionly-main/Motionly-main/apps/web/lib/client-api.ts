"use client";

import { createClient } from "@/lib/supabase/browser";

type BuildApiHeadersOptions = {
  workspaceId: string;
  userId?: string;
  accessToken?: string;
  json?: boolean;
};

export async function buildApiHeaders({
  workspaceId,
  userId,
  accessToken,
  json = true,
}: BuildApiHeadersOptions) {
  let token = accessToken;
  let sessionUserId = userId;
  if (!token) {
    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    token = session?.access_token;
    sessionUserId = session?.user.id ?? userId;
  }

  if (!token) {
    throw new Error("Your session expired. Sign in again before continuing.");
  }

  return {
    ...(json ? { "content-type": "application/json" } : {}),
    authorization: `Bearer ${token}`,
    "x-motionly-workspace-id": workspaceId,
    "x-motionly-user-id": sessionUserId ?? "",
  };
}

type EditorApiRequestOptions = {
  workspaceId: string;
  userId?: string;
  accessToken?: string;
  method?: "GET" | "POST" | "PATCH" | "DELETE";
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

export async function editorApiRequest<T>(
  path: string,
  {
    workspaceId,
    userId,
    accessToken,
    method = "GET",
    body,
    friendlyError = "That action could not be completed. Please try again.",
    json = true,
    signal,
  }: EditorApiRequestOptions,
): Promise<T> {
  let headers;
  try {
    headers = await buildApiHeaders({
      workspaceId,
      userId,
      accessToken,
      json: json && method !== "GET",
    });
  } catch (err: any) {
    throw new EditorApiError({
      message: err.message || "Authentication session expired. Please sign in again.",
      status: 401,
      code: "auth_session_expired",
      technicalMessage: err.message,
    });
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
      cache: method === "GET" ? "no-store" : undefined,
      signal,
    });
  } catch (err: any) {
    if (err.name === "AbortError" || err.message?.includes("aborted")) {
      throw new EditorApiError({
        message: "The request was cancelled.",
        status: 499,
        code: "request_aborted",
        technicalMessage: err.message,
      });
    }
    throw new EditorApiError({
      message: "Network connection lost. Please verify your internet connection.",
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
    const technicalMessage = error?.message ?? response.statusText;
    const shouldHideTechnical =
      error?.code === "request_validation_failed" ||
      /valid dictionary|extract fields|validation/i.test(technicalMessage);
    const message = shouldHideTechnical ? friendlyError : technicalMessage || friendlyError;
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
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  plan_id?: string;
  failure_reason?: string;
};

export async function pollPlanJob(
  statusUrl: string,
  options: EditorApiRequestOptions,
  onProgress?: (progress: number) => void
): Promise<string> {
  const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
  const maxRetries = 120;
  for (let i = 0; i < maxRetries; i++) {
    const response = await editorApiRequest<PlanJobStatusResponse>(
      statusUrl,
      {
        ...options,
        method: "GET",
        body: undefined,
      }
    );
    if (response.status === "succeeded") {
      if (!response.plan_id) {
        throw new Error("Job succeeded but returned no plan ID.");
      }
      return response.plan_id;
    }
    if (response.status === "failed") {
      throw new Error(response.failure_reason || "Plan generation failed.");
    }
    if (onProgress && response.progress !== undefined) {
      onProgress(response.progress);
    }
    await delay(1000);
  }
  throw new Error("Plan generation timed out.");
}

