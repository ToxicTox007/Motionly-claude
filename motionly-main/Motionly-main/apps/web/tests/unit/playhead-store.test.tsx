import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  PlayheadStoreContext,
  createPlayheadStore,
  usePlayheadMs,
} from "@/lib/playhead-store";

describe("playhead store", () => {
  it("notifies subscribers only when the value changes", () => {
    const store = createPlayheadStore(0);
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.set(250);
    store.set(250);
    store.set((previous) => previous + 250);
    expect(store.get()).toBe(500);
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    store.set(750);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("re-renders only the components that read the playhead", () => {
    const store = createPlayheadStore(0);
    let readerRenders = 0;
    let bystanderRenders = 0;
    function Reader() {
      readerRenders += 1;
      return <p>{usePlayheadMs()} ms</p>;
    }
    function Bystander() {
      bystanderRenders += 1;
      return null;
    }
    render(
      <PlayheadStoreContext.Provider value={store}>
        <Reader />
        <Bystander />
      </PlayheadStoreContext.Provider>,
    );
    act(() => store.set(1000));
    act(() => store.set(2000));
    expect(screen.getByText("2000 ms")).toBeInTheDocument();
    expect(readerRenders).toBe(3);
    expect(bystanderRenders).toBe(1);
  });
});
