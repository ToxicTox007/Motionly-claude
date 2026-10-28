import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  announceSignOut,
  clearMotionlyStorage,
  subscribeToSignOut,
} from "@/lib/session-storage";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

describe("clearMotionlyStorage", () => {
  it("removes every Motionly key and nothing else", () => {
    localStorage.setItem("motionly:editor-history:w1:p1:undo", "[]");
    localStorage.setItem("motionly:multipart-upload:v2:w1:p1", "{}");
    localStorage.setItem("motionly:brand-kit:p1", "kit");
    sessionStorage.setItem("motionly_invite_token", "secret-token");
    localStorage.setItem("other-app:setting", "keep");
    sessionStorage.setItem("unrelated", "keep");

    expect(clearMotionlyStorage()).toBe(4);
    expect(localStorage.length).toBe(1);
    expect(localStorage.getItem("other-app:setting")).toBe("keep");
    expect(sessionStorage.getItem("motionly_invite_token")).toBeNull();
    expect(sessionStorage.getItem("unrelated")).toBe("keep");
  });
});

describe("cross-tab sign-out", () => {
  it("clears this tab and signals other tabs", () => {
    localStorage.setItem("motionly:editor-mode", "pro");
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    announceSignOut();
    expect(localStorage.getItem("motionly:editor-mode")).toBeNull();
    expect(setItem).toHaveBeenCalledWith(
      "motionly:auth:signed-out",
      expect.any(String),
    );
    // The signal key is removed again straight away.
    expect(localStorage.getItem("motionly:auth:signed-out")).toBeNull();
  });

  it("notifies a subscriber when another tab signals through storage", () => {
    const onSignedOut = vi.fn();
    const unsubscribe = subscribeToSignOut(onSignedOut);
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "motionly:auth:signed-out",
        newValue: "123",
      }),
    );
    window.dispatchEvent(
      new StorageEvent("storage", { key: "unrelated", newValue: "x" }),
    );
    expect(onSignedOut).toHaveBeenCalledTimes(1);
    unsubscribe();
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "motionly:auth:signed-out",
        newValue: "456",
      }),
    );
    expect(onSignedOut).toHaveBeenCalledTimes(1);
  });
});
