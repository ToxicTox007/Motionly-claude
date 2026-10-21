/**
 * Coalesce bursts of timeline-rebuild requests into a single run.
 *
 * The editor recompiles the canonical timeline after every layer change
 * (captions, b-roll, music, effects). When several of those land in quick
 * succession we must not fire one `compile-timeline` request per change —
 * that both hammers the API and races revisions against itself. This debounces
 * the trailing edge: the latest request in a burst wins and runs once.
 *
 * Injectable timers keep it unit-testable with a fake clock.
 *
 * @param {() => Promise<void> | void} run the rebuild to perform.
 * @param {{ delayMs?: number, setTimeout?: Function, clearTimeout?: Function }} [options]
 * @returns {(() => void) & { flush: () => Promise<void>, cancel: () => void }}
 */
export function createRebuildCoalescer(run, options = {}) {
  const delayMs = options.delayMs ?? 300;
  const setTimer =
    options.setTimeout ??
    (typeof window !== "undefined"
      ? window.setTimeout.bind(window)
      : setTimeout);
  const clearTimer =
    options.clearTimeout ??
    (typeof window !== "undefined"
      ? window.clearTimeout.bind(window)
      : clearTimeout);

  let timer = null;

  const fire = () => {
    timer = null;
    return run();
  };

  const schedule = () => {
    if (timer !== null) clearTimer(timer);
    timer = setTimer(() => {
      void fire();
    }, delayMs);
  };

  schedule.flush = async () => {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
      await fire();
    }
  };

  schedule.cancel = () => {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  };

  return schedule;
}
