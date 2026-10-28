import { act, render, renderHook, waitFor } from "@testing-library/react";
import { memo, useState, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  EditorHistoryProvider,
  useEditorHistory,
  type DurableHistoryAction,
  type HistoryEntry,
} from "@/components/editor/history-context";

const STORAGE_KEY = "motionly:editor-history:ws-1:proj-1:user-1:plan-1";

function action(label: string): DurableHistoryAction {
  return {
    path: "/projects/proj-1/plans/plan-1/cuts",
    body: { cuts: [], label },
    resultKind: "plan",
    successLabel: label,
  };
}

function entry(id: string): HistoryEntry {
  return {
    id,
    label: `Edit ${id}`,
    undoAction: action(`Undo ${id}`),
    redoAction: action(`Redo ${id}`),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function setup(
  executeAction: (action: DurableHistoryAction) => Promise<string>,
  { storageKey = STORAGE_KEY, planId = "plan-1" } = {},
) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <EditorHistoryProvider
      storageKey={storageKey}
      planId={planId}
      executeAction={executeAction}
    >
      {children}
    </EditorHistoryProvider>
  );
  return renderHook(() => useEditorHistory(), { wrapper });
}

afterEach(() => {
  window.localStorage.clear();
  vi.useRealTimers();
});

describe("editor history", () => {
  it("runs a single undo when undo is pressed twice before it finishes", async () => {
    const pending = deferred<string>();
    const executeAction = vi.fn(() => pending.promise);
    const { result } = setup(executeAction);

    act(() => {
      result.current.pushHistory(entry("a"));
      result.current.pushHistory(entry("b"));
    });

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = result.current.undo();
      second = result.current.undo();
    });
    expect(result.current.isPending).toBe(true);
    expect(executeAction).toHaveBeenCalledTimes(1);
    expect(executeAction).toHaveBeenCalledWith(action("Undo b"));

    await act(async () => {
      pending.resolve("Undo b");
      await Promise.all([first, second]);
    });
    expect(executeAction).toHaveBeenCalledTimes(1);
    expect(result.current.isPending).toBe(false);
    expect(result.current.currentLabel).toBe("Undo b");
    // Only "b" moved to the redo stack; "a" is still undoable.
    expect(result.current.canUndo).toBe(true);
    expect(result.current.canRedo).toBe(true);
  });

  it("redo is also ignored while an undo is still running", async () => {
    const pending = deferred<string>();
    const executeAction = vi
      .fn<(action: DurableHistoryAction) => Promise<string>>()
      .mockResolvedValueOnce("Undo b")
      .mockReturnValueOnce(pending.promise);
    const { result } = setup(executeAction);
    act(() => {
      result.current.pushHistory(entry("a"));
      result.current.pushHistory(entry("b"));
    });
    await act(async () => {
      await result.current.undo();
    });
    expect(result.current.canRedo).toBe(true);

    let undo!: Promise<void>;
    act(() => {
      undo = result.current.undo();
    });
    await act(async () => {
      await result.current.redo();
    });
    expect(executeAction).toHaveBeenCalledTimes(2);
    expect(executeAction).not.toHaveBeenCalledWith(action("Redo b"));
    await act(async () => {
      pending.resolve("Undo a");
      await undo;
    });
  });

  it("clears the pending state and reports the error when an action fails", async () => {
    const executeAction = vi.fn(async () => {
      throw new Error("The saved editor history action could not be applied.");
    });
    const { result } = setup(executeAction);
    act(() => result.current.pushHistory(entry("a")));

    await act(async () => {
      await expect(result.current.undo()).rejects.toThrow(/could not/);
    });
    expect(result.current.isPending).toBe(false);
    expect(result.current.canUndo).toBe(true);
    expect(result.current.currentLabel).toMatch(/could not be applied/);
  });

  it("persists history under its key and drops entries saved for another plan", async () => {
    const executeAction = vi.fn(async () => "ok");
    const first = setup(executeAction);
    act(() => first.result.current.pushHistory(entry("a")));
    await waitFor(() =>
      expect(window.localStorage.getItem(`${STORAGE_KEY}:undo`)).toContain(
        '"planId":"plan-1"',
      ),
    );
    first.unmount();

    const restored = setup(executeAction);
    expect(restored.result.current.canUndo).toBe(true);
    restored.unmount();

    // Same key, different plan (e.g. a stale or tampered copy): not restored.
    const otherPlan = setup(executeAction, { planId: "plan-2" });
    expect(otherPlan.result.current.canUndo).toBe(false);
  });

  it("does not re-render consumers when the provider re-renders with the same props", () => {
    const executeAction = vi.fn(async () => "ok");
    let consumerRenders = 0;
    const Consumer = memo(function Consumer() {
      useEditorHistory();
      consumerRenders += 1;
      return null;
    });
    const child = <Consumer />;
    let bump!: () => void;
    function Parent() {
      const [, setCount] = useState(0);
      bump = () => setCount((count) => count + 1);
      return (
        <EditorHistoryProvider
          storageKey={STORAGE_KEY}
          planId="plan-1"
          executeAction={executeAction}
        >
          {child}
        </EditorHistoryProvider>
      );
    }
    render(<Parent />);
    const before = consumerRenders;
    act(() => bump());
    act(() => bump());
    expect(consumerRenders).toBe(before);
  });
});
