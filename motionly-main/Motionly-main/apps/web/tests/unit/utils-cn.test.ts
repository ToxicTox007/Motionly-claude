import { describe, expect, it } from "vitest";
import { cn } from "@/lib/utils";

describe("cn and the custom type scale", () => {
  it("treats text-xxs and text-2xs as font sizes, not colours", () => {
    expect(cn("text-xxs text-muted-foreground")).toBe(
      "text-xxs text-muted-foreground",
    );
    expect(cn("text-2xs text-primary")).toBe("text-2xs text-primary");
  });

  it("lets a later font size replace an earlier one", () => {
    expect(cn("text-xxs", "text-sm")).toBe("text-sm");
    expect(cn("text-xs", "text-2xs")).toBe("text-2xs");
  });

  it("still lets a later colour replace an earlier one", () => {
    expect(cn("text-xxs text-foreground", "text-danger")).toBe(
      "text-xxs text-danger",
    );
  });
});
