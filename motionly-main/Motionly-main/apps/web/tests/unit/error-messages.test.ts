import { describe, expect, it } from "vitest";
import {
  knownErrorFor,
  messageFor,
  noticeFor,
  searchParam,
} from "@/lib/error-messages";

describe("messageFor", () => {
  it("maps a known code to its copy", () => {
    expect(messageFor("projects", "title_required")).toBe(
      "Give the project a title.",
    );
    expect(messageFor("dashboard", "admin_access_denied")).toMatch(
      /do not have access/,
    );
  });

  it("falls back to shared copy, then to a generic message", () => {
    expect(messageFor("billing", "network_error")).toMatch(/could not reach/);
    expect(messageFor("billing", undefined)).toMatch(/could not be completed/);
  });

  it("never echoes an unknown code back to the page", () => {
    const crafted = "Your_account_is_locked_call_0800_000_000";
    const message = messageFor("account", crafted);
    expect(message).not.toContain("locked");
    expect(message).not.toContain(crafted);
    expect(knownErrorFor("account", crafted)).toBeNull();
  });
});

describe("noticeFor", () => {
  it("returns copy only for known notices", () => {
    expect(noticeFor("billing", "cancel_scheduled")).toMatch(/cancelled/);
    expect(noticeFor("billing", "you_won_a_prize")).toBeNull();
    expect(noticeFor("billing", null)).toBeNull();
  });
});

describe("searchParam", () => {
  it("reads the first string value", () => {
    expect(searchParam({ a: "1" }, "a")).toBe("1");
    expect(searchParam({ a: ["2", "3"] }, "a")).toBe("2");
    expect(searchParam({}, "a")).toBeNull();
  });
});
