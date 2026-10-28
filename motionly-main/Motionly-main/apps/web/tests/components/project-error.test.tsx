import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ProjectError from "@/app/(app)/projects/[projectId]/error";

function serverError(message: string, digest?: string) {
  return Object.assign(new Error(message), digest ? { digest } : {});
}

describe("project error boundary", () => {
  it("retries by re-fetching the project's server data", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const retry = vi.fn();
    const reset = vi.fn();
    render(
      <ProjectError
        error={serverError("boom", "digest-1")}
        retry={retry}
        reset={reset}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: /retry loading/i }),
    );
    expect(retry).toHaveBeenCalledTimes(1);
    expect(reset).not.toHaveBeenCalled();
  });

  it("shows the digest as a reference, never the error message", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ProjectError
        error={serverError("Motionly API request failed (500)", "digest-7")}
        retry={vi.fn()}
        reset={vi.fn()}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("digest-7");
    expect(alert).not.toHaveTextContent(/API request failed/);
    expect(
      screen.getByRole("link", { name: "Back to projects" }),
    ).toHaveAttribute("href", "/projects");
  });
});
