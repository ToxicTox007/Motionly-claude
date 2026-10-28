import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks, router } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/lib/supabase/browser", () => ({
  createClient: () => ({
    auth: {
      getSession: async () => ({
        data: { session: { access_token: "jwt" } },
      }),
    },
  }),
}));
const invite = vi.hoisted(() => ({ acceptInvite: vi.fn() }));
vi.mock("@/app/invite/actions", () => invite);

const { InviteClient } = await import("@/app/invite/InviteClient");
const { INVITE_TOKEN_STORAGE_KEY } = await import("@/lib/team.mjs");

beforeEach(() => {
  resetNextMocks();
  invite.acceptInvite.mockReset();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/invite#token=tok-1");
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

async function joinWith(result: unknown) {
  invite.acceptInvite.mockResolvedValue(result);
  const user = userEvent.setup();
  render(<InviteClient />);
  await user.click(
    await screen.findByRole("button", { name: "Join workspace" }),
  );
}

describe("InviteClient", () => {
  it.each(["invitation_not_found", "invitation_already_used"])(
    "forgets a dead invite token (%s) and stops offering to join",
    async (code) => {
      await joinWith({ ok: false, error: code, signedIn: true });
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      expect(
        window.sessionStorage.getItem(INVITE_TOKEN_STORAGE_KEY),
      ).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Join workspace" }),
      ).toBeNull();
      expect(
        screen.getByRole("link", { name: "Go to dashboard" }),
      ).toHaveAttribute("href", "/dashboard");
    },
  );

  it("keeps the token and offers a retry when the failure is temporary", async () => {
    await joinWith({
      ok: false,
      error: "invite_accept_failed",
      signedIn: true,
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Please try again/,
    );
    expect(window.sessionStorage.getItem(INVITE_TOKEN_STORAGE_KEY)).toBe(
      "tok-1",
    );
    expect(
      screen.getByRole("button", { name: "Join workspace" }),
    ).toBeInTheDocument();
  });

  it("keeps the token when the workspace is out of seats (the owner can upgrade)", async () => {
    await joinWith({ ok: false, error: "seat_limit_reached", signedIn: true });
    expect(await screen.findByRole("alert")).toHaveTextContent(/no seats left/);
    expect(window.sessionStorage.getItem(INVITE_TOKEN_STORAGE_KEY)).toBe(
      "tok-1",
    );
  });

  it("clears the token after joining", async () => {
    await joinWith({ ok: true, workspaceId: "ws-1", workspaceName: "Acme" });
    expect(await screen.findByText(/joined Acme/)).toBeInTheDocument();
    expect(window.sessionStorage.getItem(INVITE_TOKEN_STORAGE_KEY)).toBeNull();
    expect(router.replace).toHaveBeenCalledWith("/dashboard");
  });
});
