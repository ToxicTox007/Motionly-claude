"use client";

import { useEffect, useRef } from "react";
import { startAdaptivePolling } from "@/lib/client-api";

type PollWhileOptions = {
  intervalMs: number;
  maxIntervalMs?: number;
  backoffStepMs?: number;
  /** Poll right away when polling starts. Off by default: the caller usually
   * just loaded the data that told it to start polling. */
  immediate?: boolean;
};

/**
 * Polls `load` on an adaptive, visibility-aware interval for as long as
 * `active` is true. Only `active` and the options start or stop the poller:
 * the latest `load` is read from a ref, so new data (and a new loader
 * identity) from a poll does not restart the timer and fire an extra request.
 */
export function usePollWhile(
  active: boolean,
  load: (signal: AbortSignal) => unknown,
  {
    intervalMs,
    maxIntervalMs = intervalMs,
    backoffStepMs = 500,
    immediate = false,
  }: PollWhileOptions,
) {
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  useEffect(() => {
    if (!active) return;
    return startAdaptivePolling(
      async (signal) => {
        await loadRef.current(signal);
      },
      { intervalMs, maxIntervalMs, backoffStepMs, immediate },
    );
  }, [active, backoffStepMs, immediate, intervalMs, maxIntervalMs]);
}
