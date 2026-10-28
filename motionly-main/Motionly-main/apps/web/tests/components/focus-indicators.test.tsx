import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Input, Select, Textarea } from "@/components/brutal-ui";
import { Button, ButtonLink, IconButton } from "@/components/button";

// jsdom does not paint, so these check the classes that produce the ring; the
// browser suite checks the rendered indicator (tests/browser).
const VISIBLE_RING = [
  "focus-visible:ring-2",
  "focus-visible:ring-focus",
  "focus-visible:ring-offset-2",
];

describe("visible keyboard focus (WCAG 2.4.7)", () => {
  it("every button primitive shows a focus-colour ring", () => {
    render(
      <>
        <Button>Save</Button>
        <ButtonLink href="/x">Open</ButtonLink>
        <IconButton label="Close">
          <svg aria-hidden="true" />
        </IconButton>
      </>,
    );
    for (const element of [
      screen.getByRole("button", { name: "Save" }),
      screen.getByRole("link", { name: "Open" }),
      screen.getByRole("button", { name: "Close" }),
    ]) {
      for (const cls of VISIBLE_RING) expect(element).toHaveClass(cls);
      // The old focus:outline-none (0-2-0) beat the global :focus-visible rule.
      expect(element).not.toHaveClass("focus:outline-none");
    }
  });

  it("form fields replace the near-invisible primary/30 ring", () => {
    render(
      <>
        <label>
          Name
          <Input />
        </label>
        <label>
          Notes
          <Textarea />
        </label>
        <label>
          Kind
          <Select>
            <option>a</option>
          </Select>
        </label>
      </>,
    );
    for (const field of [
      screen.getByLabelText("Name"),
      screen.getByLabelText("Notes"),
      screen.getByLabelText("Kind"),
    ]) {
      expect(field).toHaveClass("focus:ring-2", "focus:ring-focus");
      expect(field).not.toHaveClass("focus:ring-primary/30");
    }
  });
});
