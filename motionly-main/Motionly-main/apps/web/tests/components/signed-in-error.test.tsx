import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SignedInError from "@/app/(app)/error";

function serverError(message: string, digest?: string) {
  return Object.assign(new Error(message), digest ? { digest } : {});
}

describe("signed-in error boundary", () => {
  it("retries by re-fetching the segment", async () => {
    const retry = vi.fn();
    const reset = vi.fn();
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <SignedInError
        error={serverError("boom", "123abc")}
        retry={retry}
        reset={reset}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(reset).not.toHaveBeenCalled();
  });

  it("shows the digest as a reference but never the error message", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <SignedInError
        error={serverError("SELECT * FROM secrets failed", "123abc")}
        retry={vi.fn()}
        reset={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("123abc");
    expect(screen.queryByText(/secrets/)).not.toBeInTheDocument();
  });

  it("links back to the dashboard and home", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <SignedInError error={"thrown string"} retry={vi.fn()} reset={vi.fn()} />,
    );
    expect(
      screen.getByRole("link", { name: "Go to dashboard" }),
    ).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute(
      "href",
      "/",
    );
  });
});
