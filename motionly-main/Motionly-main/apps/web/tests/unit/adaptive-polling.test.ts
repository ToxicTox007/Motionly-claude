import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startAdaptivePolling } from "@/lib/client-api";

// Fake timers drive the poller; advanceTimersByTimeAsync also flushes the
// promise chain between turns.
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
});

async function elapse(ms: number) {
  await vi.advanceTimersByTimeAsync(ms);
}

describe("startAdaptivePolling", () => {
  it("polls immediately, then at a steady interval while turns succeed", async () => {
    const callback = vi.fn(async () => {});
    const stop = startAdaptivePolling(callback, {
      intervalMs: 3000,
      maxIntervalMs: 9000,
      backoffStepMs: 500,
    });
    await elapse(0);
    expect(callback).toHaveBeenCalledTimes(1);
    await elapse(3000);
    expect(callback).toHaveBeenCalledTimes(2);
    await elapse(3000);
    expect(callback).toHaveBeenCalledTimes(3);
    await elapse(3000);
    expect(callback).toHaveBeenCalledTimes(4);
    stop();
  });

  it("backs off after failures and returns to the base interval after a success", async () => {
    let fail = true;
    const callback = vi.fn(async () => {
      if (fail) throw new Error("offline");
    });
    const stop = startAdaptivePolling(callback, {
      intervalMs: 1000,
      maxIntervalMs: 5000,
      backoffStepMs: 1000,
    });
    await elapse(0); // turn 1 fails -> next in 1000
    await elapse(1000); // turn 2 fails -> next in 2000
    expect(callback).toHaveBeenCalledTimes(2);
    await elapse(1999);
    expect(callback).toHaveBeenCalledTimes(2);
    fail = false;
    await elapse(1); // turn 3 succeeds -> back to 1000
    expect(callback).toHaveBeenCalledTimes(3);
    await elapse(1000);
    expect(callback).toHaveBeenCalledTimes(4);
    await elapse(1000);
    expect(callback).toHaveBeenCalledTimes(5);
    stop();
  });

  it("never exceeds the maximum interval", async () => {
    const callback = vi.fn(async () => {
      throw new Error("down");
    });
    const stop = startAdaptivePolling(callback, {
      intervalMs: 1000,
      maxIntervalMs: 2000,
      backoffStepMs: 5000,
    });
    await elapse(0);
    await elapse(1000);
    await elapse(2000);
    await elapse(2000);
    expect(callback).toHaveBeenCalledTimes(4);
    stop();
  });

  it("does not start a turn while the previous one is still running", async () => {
    let finish!: () => void;
    const callback = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const stop = startAdaptivePolling(callback, { intervalMs: 1000 });
    await elapse(0);
    await elapse(10_000);
    expect(callback).toHaveBeenCalledTimes(1);
    finish();
    await elapse(1000);
    expect(callback).toHaveBeenCalledTimes(2);
    stop();
  });

  it("stop aborts the running turn and schedules nothing more", async () => {
    let signal: AbortSignal | undefined;
    const callback = vi.fn(async (next: AbortSignal) => {
      signal = next;
    });
    const stop = startAdaptivePolling(callback, { intervalMs: 1000 });
    await elapse(0);
    stop();
    expect(signal?.aborted).toBe(true);
    await elapse(10_000);
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("can wait one interval before the first turn", async () => {
    const callback = vi.fn(async () => {});
    const stop = startAdaptivePolling(callback, {
      intervalMs: 1000,
      immediate: false,
    });
    await elapse(999);
    expect(callback).not.toHaveBeenCalled();
    await elapse(1);
    expect(callback).toHaveBeenCalledTimes(1);
    stop();
  });
});
