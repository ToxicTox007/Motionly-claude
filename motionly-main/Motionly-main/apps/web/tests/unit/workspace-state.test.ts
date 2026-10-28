import { describe, expect, it } from "vitest";
import { resolveWorkspaceState } from "@/lib/workspace-state";

const one = [{ workspaceId: "w1" }];
const two = [{ workspaceId: "w1" }, { workspaceId: "w2" }];

describe("resolveWorkspaceState", () => {
  it("reports no workspace instead of looping when there are no memberships", () => {
    expect(resolveWorkspaceState([], undefined)).toEqual({ kind: "none" });
    expect(resolveWorkspaceState([], "w1")).toEqual({ kind: "none" });
  });

  it("selects the only workspace when nothing is chosen", () => {
    expect(resolveWorkspaceState(one, undefined)).toEqual({
      kind: "selected",
      workspaceId: "w1",
    });
  });

  it("selects the chosen workspace when it is an active membership", () => {
    expect(resolveWorkspaceState(two, " w2 ")).toEqual({
      kind: "selected",
      workspaceId: "w2",
    });
  });

  it("asks the viewer to pick when there are several and none is chosen", () => {
    expect(resolveWorkspaceState(two, undefined)).toEqual({ kind: "pick" });
  });

  it("asks the viewer to pick when the chosen workspace is stale", () => {
    expect(resolveWorkspaceState(one, "gone")).toEqual({ kind: "pick" });
  });
});
