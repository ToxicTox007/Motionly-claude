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
  const headers = await buildApiHeaders({
    workspaceId,
    userId,
    accessToken,
    json: json && method !== "GET",
  });
  const response = await fetch(path, {
    method,
    headers,
    body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
    cache: method === "GET" ? "no-store" : undefined,
    signal,
  });
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
