import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePollWhile } from "@/hooks/usePollWhile";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

async function elapse(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

type Props = { active: boolean; load: () => Promise<void> };

function setup(initial: Props) {
  return renderHook(
    ({ active, load }: Props) =>
      usePollWhile(active, load, { intervalMs: 3000, maxIntervalMs: 9000 }),
    { initialProps: initial },
  );
}

describe("usePollWhile", () => {
  it("does nothing while inactive", async () => {
    const load = vi.fn(async () => {});
    setup({ active: false, load });
    await elapse(30_000);
    expect(load).not.toHaveBeenCalled();
  });

  it("waits one interval, then polls at that interval while active", async () => {
    const load = vi.fn(async () => {});
    setup({ active: true, load });
    await elapse(2999);
    expect(load).not.toHaveBeenCalled();
    await elapse(1);
    expect(load).toHaveBeenCalledTimes(1);
    await elapse(3000);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("does not restart or poll early when the loader or data changes", async () => {
    const first = vi.fn(async () => {});
    const { rerender } = setup({ active: true, load: first });
    await elapse(3000);
    expect(first).toHaveBeenCalledTimes(1);

    // Each poll produces new data and a new loader identity; neither may
    // trigger an immediate extra request.
    const second = vi.fn(async () => {});
    rerender({ active: true, load: second });
    await elapse(10);
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).not.toHaveBeenCalled();

    await elapse(2990);
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("stops when it becomes inactive", async () => {
    const load = vi.fn(async () => {});
    const { rerender } = setup({ active: true, load });
    await elapse(3000);
    rerender({ active: false, load });
    await elapse(30_000);
    expect(load).toHaveBeenCalledTimes(1);
  });
});
