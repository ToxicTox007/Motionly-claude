import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useRevisionTracking } from "@/hooks/useRevisionTracking";
import { RevisionConflictError } from "@/lib/client-api";

type Props = {
  projectId: string;
  planId: string | null;
  seedRevision: number | null;
};

function setup(initial: Props, fetchLatestRevision = vi.fn(async () => 9)) {
  return renderHook(
    ({ projectId, planId, seedRevision }: Props) =>
      useRevisionTracking(projectId, planId, fetchLatestRevision, seedRevision),
    { initialProps: initial },
  );
}

function conflict(expected: number, current: number) {
  return new RevisionConflictError({
    expectedRevision: expected,
    currentRevision: current,
  });
}

describe("useRevisionTracking", () => {
  it("seeds the expected revision from the active plan, so the first save is guarded", () => {
    const { result } = setup({
      projectId: "p1",
      planId: "plan-1",
      seedRevision: 7,
    });
    expect(result.current.planRevision).toBe(7);
    expect(result.current.getExpectedRevision()).toBe(7);
    expect(result.current.conflictState).toBeNull();
  });

  it("starts empty when the plan has no revision (older API)", () => {
    const { result } = setup({
      projectId: "p1",
      planId: "plan-1",
      seedRevision: null,
    });
    expect(result.current.planRevision).toBeNull();
    expect(result.current.getExpectedRevision()).toBeUndefined();
  });

  it("re-seeds when the active plan changes and drops the old plan's revision", () => {
    const { result, rerender } = setup({
      projectId: "p1",
      planId: "plan-1",
      seedRevision: 3,
    });
    act(() => result.current.updateRevision(4, "plan"));
    expect(result.current.getExpectedRevision()).toBe(4);

    rerender({ projectId: "p1", planId: "plan-2", seedRevision: 11 });
    expect(result.current.planRevision).toBe(11);
    expect(result.current.getExpectedRevision()).toBe(11);

    rerender({ projectId: "p1", planId: "plan-3", seedRevision: null });
    expect(result.current.planRevision).toBeNull();
    expect(result.current.getExpectedRevision()).toBeUndefined();
  });

  it("keeps a newer revision from a save when the seed for the same plan is older", () => {
    const { result, rerender } = setup({
      projectId: "p1",
      planId: "plan-1",
      seedRevision: 3,
    });
    act(() => result.current.updateRevision(5, "plan"));
    rerender({ projectId: "p1", planId: "plan-1", seedRevision: 3 });
    expect(result.current.getExpectedRevision()).toBe(5);
  });

  it("updateRevision tracks plan and media revisions and ignores null", () => {
    const { result } = setup({
      projectId: "p1",
      planId: "plan-1",
      seedRevision: 1,
    });
    act(() => result.current.updateRevision(2, "plan"));
    act(() => result.current.updateRevision(8, "media"));
    act(() => result.current.updateRevision(null, "plan"));
    expect(result.current.planRevision).toBe(2);
    expect(result.current.mediaRevision).toBe(8);
    expect(result.current.getExpectedRevision()).toBe(2);
    expect(result.current.lastSyncedAt).not.toBeNull();
  });

  it("handleConflict records revision conflicts only", () => {
    const { result } = setup({
      projectId: "p1",
      planId: "plan-1",
      seedRevision: 1,
    });
    let handled = false;
    act(() => {
      handled = result.current.handleConflict(conflict(1, 2), "cuts");
    });
    expect(handled).toBe(true);
    expect(result.current.conflictState).toMatchObject({
      expectedRevision: 1,
      currentRevision: 2,
      source: "cuts",
    });
    expect(result.current.handleConflict(new Error("boom"), "cuts")).toBe(
      false,
    );
    act(() => result.current.clearConflict());
    expect(result.current.conflictState).toBeNull();
  });

  it("forceSave resends against the latest revision and tracks it", async () => {
    const fetchLatest = vi.fn(async () => 9);
    const retry = vi.fn(async () => {});
    const { result } = setup(
      { projectId: "p1", planId: "plan-1", seedRevision: 1 },
      fetchLatest,
    );
    act(() => {
      result.current.handleConflict(conflict(1, 9), "cuts", { retry });
    });
    await act(async () => {
      await result.current.forceSave();
    });
    expect(retry).toHaveBeenCalledWith(9);
    expect(result.current.getExpectedRevision()).toBe(9);
    expect(result.current.conflictState).toBeNull();
  });
});
