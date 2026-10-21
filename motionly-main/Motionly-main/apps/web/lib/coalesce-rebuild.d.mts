export interface RebuildCoalescerOptions {
  delayMs?: number;
  setTimeout?: (handler: () => void, timeout: number) => unknown;
  clearTimeout?: (handle: unknown) => void;
}

export type RebuildCoalescer = (() => void) & {
  flush: () => Promise<void>;
  cancel: () => void;
};

/**
 * Coalesce bursts of timeline-rebuild requests into a single trailing run.
 */
export function createRebuildCoalescer(
  run: () => Promise<void> | void,
  options?: RebuildCoalescerOptions,
): RebuildCoalescer;
