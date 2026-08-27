"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  WorkflowCommand,
  WorkflowCommandResponse,
  WorkflowDetail,
  WorkflowList,
} from "@motionly/shared";
import { env } from "@/lib/env";
import { editorApiRequest, startAdaptivePolling } from "@/lib/client-api";
import {
  assertCanonicalWorkflowIdentity,
  assertWorkflowCommandTransition,
  selectLatestFirstDraftWorkflow,
  workflowCommandIdempotencyKey,
} from "@/lib/workflow-ui.mjs";

const ACTIVE_WORKFLOW_STATES = new Set([
  "uploaded",
  "analyzing",
  "proposal_ready",
  "awaiting_approval",
  "assembling",
  "draft_rendering",
]);

export const WORKFLOW_STATUS_TRANSPORT = "authenticated-adaptive-polling" as const;

type UseWorkflowStatusOptions = {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialWorkflow: WorkflowDetail | null;
};

export type WorkflowStatusController = {
  workflow: WorkflowDetail | null;
  error: string | null;
  pendingCommand: WorkflowCommand | null;
  transport: typeof WORKFLOW_STATUS_TRANSPORT;
  refresh: (signal?: AbortSignal) => Promise<WorkflowDetail | null>;
  runCommand: (command: WorkflowCommand) => Promise<WorkflowDetail | null>;
};

export function useWorkflowStatus({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialWorkflow,
}: UseWorkflowStatusOptions): WorkflowStatusController {
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(() => {
    if (!initialWorkflow) return null;
    try {
      return assertCanonicalWorkflowIdentity(initialWorkflow, { projectId });
    } catch {
      return null;
    }
  });
  const [error, setError] = useState<string | null>(null);
  const [pendingCommand, setPendingCommand] =
    useState<WorkflowCommand | null>(null);
  const refreshSequenceRef = useRef(0);
  const workflowRef = useRef(workflow);
  const pendingCommandRef = useRef<WorkflowCommand | null>(null);
  useEffect(() => {
    workflowRef.current = workflow;
  }, [workflow]);

  const requestOptions = useCallback(
    (signal?: AbortSignal) => ({
      workspaceId,
      userId,
      accessToken: apiAccessToken,
      signal,
    }),
    [apiAccessToken, userId, workspaceId],
  );

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const sequence = ++refreshSequenceRef.current;
      try {
        const list = await editorApiRequest<WorkflowList>(
          `${env.apiBaseUrl}/projects/${projectId}/workflows?limit=50`,
          requestOptions(signal),
        );
        const selected = selectLatestFirstDraftWorkflow(
          list.workflows ?? [],
          projectId,
        );
        if (!selected) {
          if (sequence === refreshSequenceRef.current) {
            setWorkflow(null);
            setError(null);
          }
          return null;
        }
        const detail = await editorApiRequest<WorkflowDetail>(
          `${env.apiBaseUrl}/projects/${projectId}/workflows/${encodeURIComponent(selected.id)}`,
          requestOptions(signal),
        );
        assertCanonicalWorkflowIdentity(detail, {
          projectId,
          expectedWorkflowId: selected.id,
        });
        if (sequence === refreshSequenceRef.current) {
          setWorkflow(detail);
          setError(null);
        }
        return detail;
      } catch (caught) {
        if (signal?.aborted) return null;
        if (sequence === refreshSequenceRef.current) {
          setError((caught as Error).message);
        }
        throw caught;
      }
    },
    [projectId, requestOptions],
  );

  useEffect(() => {
    // Workflow streaming is not exposed by the backend. This authenticated,
    // visibility/offline-aware transport is the explicit fallback; credentials
    // are supplied only through editorApiRequest headers, never in a URL.
    const active =
      !workflowRef.current ||
      ACTIVE_WORKFLOW_STATES.has(workflowRef.current.state);
    const stopPolling = startAdaptivePolling(
      async (signal) => {
        await refresh(signal).catch(() => undefined);
      },
      active
        ? { intervalMs: 2_500, maxIntervalMs: 8_000, backoffStepMs: 500 }
        : { intervalMs: 30_000, maxIntervalMs: 60_000, backoffStepMs: 5_000 },
    );
    const refreshOnFocus = () => {
      if (document.visibilityState === "visible" && navigator.onLine) {
        void refresh().catch(() => undefined);
      }
    };
    window.addEventListener("focus", refreshOnFocus);
    document.addEventListener("visibilitychange", refreshOnFocus);
    window.addEventListener("online", refreshOnFocus);
    return () => {
      stopPolling();
      window.removeEventListener("focus", refreshOnFocus);
      document.removeEventListener("visibilitychange", refreshOnFocus);
      window.removeEventListener("online", refreshOnFocus);
    };
  }, [refresh, workflow?.id, workflow?.state]);

  const runCommand = useCallback(
    async (command: WorkflowCommand) => {
      const current = workflowRef.current;
      if (!current || pendingCommandRef.current) return null;
      if (!current.allowed_actions.includes(command)) {
        setError(`Workflow cannot ${command} from ${current.state}.`);
        return null;
      }
      pendingCommandRef.current = command;
      setPendingCommand(command);
      setError(null);
      try {
        const response = await editorApiRequest<WorkflowCommandResponse>(
          `${env.apiBaseUrl}/projects/${projectId}/workflows/${encodeURIComponent(current.id)}/${command}`,
          {
            ...requestOptions(),
            method: "POST",
            friendlyError: `The workflow could not be ${command === "cancel" ? "cancelled" : "retried"}.`,
            body: {
              expected_revision: current.revision,
              idempotency_key: workflowCommandIdempotencyKey(current, command),
            },
          },
        );
        const next = assertWorkflowCommandTransition(
          current,
          response.workflow,
          command,
        );
        // Retry intentionally returns a new run ID and replaces canonical state.
        refreshSequenceRef.current += 1;
        workflowRef.current = next;
        setWorkflow(next);
        return next;
      } catch (caught) {
        setError((caught as Error).message);
        void refresh().catch(() => undefined);
        return null;
      } finally {
        pendingCommandRef.current = null;
        setPendingCommand(null);
      }
    },
    [projectId, refresh, requestOptions],
  );

  return {
    workflow,
    error,
    pendingCommand,
    transport: WORKFLOW_STATUS_TRANSPORT,
    refresh,
    runCommand,
  };
}
