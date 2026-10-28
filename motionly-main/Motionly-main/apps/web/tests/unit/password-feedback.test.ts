import { describe, expect, it } from "vitest";
import { passwordFeedback } from "@/lib/password-feedback";

describe("passwordFeedback", () => {
  it("never calls a password strong that the policy refuses", () => {
    for (const refused of ["short", "password1", "aaaaaaaaaaaaaaaa"]) {
      const feedback = passwordFeedback(refused);
      expect(feedback.score, refused).toBe(1);
      expect(feedback.problem, refused).toBeTruthy();
    }
  });

  it("refuses a password containing the email name", () => {
    expect(
      passwordFeedback("jordan-loves-reels", "jordan@example.com").problem,
    ).toMatch(/email/);
  });

  it("grades acceptable passwords by length and variety", () => {
    expect(passwordFeedback("Plum-42x").score).toBe(2);
    expect(passwordFeedback("Plum-orch-4").score).toBe(3);
    expect(passwordFeedback("Plum-orchard-42-Xy").score).toBe(4);
  });
});
