import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/app/fonts", () => ({
  jakarta: { variable: "font-jakarta-variable" },
}));

const { default: GlobalError } = await import("@/app/global-error");

function serverError(message: string, digest?: string) {
  return Object.assign(new Error(message), digest ? { digest } : {});
}

describe("global error page", () => {
  it("uses the app font and design tokens instead of hard-coded colours", () => {
    const { container } = render(
      <GlobalError error={serverError("boom", "d-1")} reset={() => {}} />,
      { container: document.documentElement },
    );
    const html = container.ownerDocument.documentElement;
    expect(html.querySelector("body")?.className).toMatch(/bg-background/);
    expect(document.querySelector("html")?.className).toContain(
      "font-jakarta-variable",
    );
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("offers Home, the dashboard and a retry, and shows the reference", async () => {
    const reset = vi.fn();
    render(<GlobalError error={serverError("boom", "d-2")} reset={reset} />, {
      container: document.documentElement,
    });
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(
      screen.getByRole("link", { name: /Go to dashboard/ }),
    ).toHaveAttribute("href", "/dashboard");
    await userEvent.click(screen.getByRole("button", { name: /Try again/ }));
    expect(reset).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/d-2/)).toBeInTheDocument();
    expect(screen.queryByText("boom")).toBeNull();
  });
});
