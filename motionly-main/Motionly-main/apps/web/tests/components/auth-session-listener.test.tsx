import { render } from "@testing-library/react";
import { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

type AuthCallback = (event: string) => void;
const listeners: AuthCallback[] = [];
const unsubscribe = vi.fn();
vi.mock("@/lib/supabase/browser", () => ({
  createClient: () => ({
    auth: {
      onAuthStateChange: (callback: AuthCallback) => {
        listeners.push(callback);
        return { data: { subscription: { unsubscribe } } };
      },
    },
  }),
}));

const { AuthSessionListener } =
  await import("@/components/auth-session-listener");

beforeEach(() => {
  listeners.length = 0;
  unsubscribe.mockClear();
  localStorage.clear();
});

describe("AuthSessionListener", () => {
  it("clears Motionly state and leaves a signed-in page when Auth signs out", () => {
    window.history.replaceState(null, "", "/projects/p1?tab=captions");
    localStorage.setItem("motionly:editor-history:w1:p1:undo", "[]");
    const navigate = vi.fn();
    render(<AuthSessionListener navigate={navigate} />);

    act(() => listeners[0]("TOKEN_REFRESHED"));
    expect(navigate).not.toHaveBeenCalled();

    act(() => listeners[0]("SIGNED_OUT"));
    expect(localStorage.length).toBe(0);
    expect(navigate).toHaveBeenCalledWith(
      "/login?next=%2Fprojects%2Fp1%3Ftab%3Dcaptions",
    );
  });

  it("follows another tab's sign-out, but leaves public pages alone", () => {
    const navigate = vi.fn();
    window.history.replaceState(null, "", "/pricing");
    render(<AuthSessionListener navigate={navigate} />);
    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: "motionly:auth:signed-out",
          newValue: "1",
        }),
      );
    });
    expect(navigate).not.toHaveBeenCalled();

    window.history.replaceState(null, "", "/dashboard");
    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: "motionly:auth:signed-out",
          newValue: "2",
        }),
      );
    });
    expect(navigate).toHaveBeenCalledWith("/login?next=%2Fdashboard");
  });

  it("unsubscribes on unmount", () => {
    const { unmount } = render(<AuthSessionListener navigate={vi.fn()} />);
    unmount();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });
});
