// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  navigationMock,
  RedirectError,
  resetNextMocks,
} from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
const state = { context: null as unknown };
vi.mock("@/lib/api-auth", () => ({
  getWorkspaceApiContext: vi.fn(async () => state.context),
}));

const { requireWorkspaceApiContext } = await import("@/lib/require-context");

beforeEach(() => {
  resetNextMocks();
  state.context = null;
});

describe("requireWorkspaceApiContext", () => {
  it("fails closed: an unknown viewer signs in again instead of acting as owner", async () => {
    await expect(
      requireWorkspaceApiContext("/account/team"),
    ).rejects.toMatchObject(new RedirectError("/login?next=%2Faccount%2Fteam"));
  });

  it("returns the real context and role", async () => {
    state.context = { role: "member", workspaceId: "w1" };
    expect(await requireWorkspaceApiContext("/account/team")).toEqual({
      role: "member",
      workspaceId: "w1",
    });
  });
});
