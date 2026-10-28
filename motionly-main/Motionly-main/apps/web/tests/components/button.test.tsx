import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button, ButtonLink, IconButton } from "@/components/button";

describe("Button", () => {
  it("renders a native button that runs its handler", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("applies the variant and size classes", () => {
    render(
      <Button variant="danger" size="lg">
        Delete
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Delete" });
    expect(button).toHaveClass("bg-danger");
    expect(button).toHaveClass("min-h-12");
  });

  it("does not run its handler while disabled", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("ButtonLink", () => {
  it("renders a link to its href with button styling", () => {
    render(<ButtonLink href="/projects/new">New project</ButtonLink>);
    const link = screen.getByRole("link", { name: "New project" });
    expect(link).toHaveAttribute("href", "/projects/new");
    expect(link).toHaveClass("bg-primary");
  });
});

describe("IconButton", () => {
  it("is named by its label", () => {
    render(
      <IconButton label="Close panel">
        <svg aria-hidden="true" />
      </IconButton>,
    );
    expect(
      screen.getByRole("button", { name: "Close panel" }),
    ).toBeInTheDocument();
  });
});
