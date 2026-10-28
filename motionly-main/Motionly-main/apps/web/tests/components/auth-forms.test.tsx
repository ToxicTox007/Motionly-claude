import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  navigationMock,
  navigationState,
  resetNextMocks,
} from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/app/(auth)/actions", () => ({
  login: vi.fn(),
  signup: vi.fn(),
  requestPasswordReset: vi.fn(),
}));

const { default: LoginPage } = await import("@/app/(auth)/login/page");
const { default: SignupPage } = await import("@/app/(auth)/signup/page");
const { default: ForgotPasswordPage } =
  await import("@/app/(auth)/forgot-password/page");

beforeEach(() => resetNextMocks());

describe("auth forms work with password managers (WCAG 1.3.5)", () => {
  it("login", () => {
    render(<LoginPage />);
    expect(screen.getByLabelText("Email")).toHaveAttribute(
      "autocomplete",
      "email",
    );
    expect(screen.getByLabelText("Password")).toHaveAttribute(
      "autocomplete",
      "current-password",
    );
  });

  it("signup", () => {
    render(<SignupPage />);
    expect(screen.getByLabelText("Full name")).toHaveAttribute(
      "autocomplete",
      "name",
    );
    expect(screen.getByLabelText("Email")).toHaveAttribute(
      "autocomplete",
      "email",
    );
    expect(screen.getByLabelText("Password")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
  });

  it("forgot password", () => {
    render(<ForgotPasswordPage />);
    expect(screen.getByLabelText("Email")).toHaveAttribute(
      "autocomplete",
      "email",
    );
  });
});

describe("login validation", () => {
  it("does not apply the sign-up length rule to an existing password", async () => {
    render(<LoginPage />);
    const password = screen.getByLabelText("Password");
    await userEvent.type(password, "short");
    await userEvent.tab();
    expect(screen.queryByText(/at least 8/i)).not.toBeInTheDocument();
  });

  it("keeps the destination in the form after an error", () => {
    navigationState.searchParams = new URLSearchParams({
      error: "invalid_credentials",
      next: "/invite",
    });
    const { container } = render(<LoginPage />);
    expect(container.querySelector('input[name="next"]')).toHaveAttribute(
      "value",
      "/invite",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      /invalid email or password/i,
    );
  });
});

describe("signup strength meter", () => {
  it("follows the server policy instead of length alone", async () => {
    render(<SignupPage />);
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    expect(screen.getByText("Password strength: Too weak")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Password"));
    await userEvent.type(
      screen.getByLabelText("Password"),
      "Plum-orchard-42-Xy",
    );
    expect(
      screen.getByText("Password strength: Very strong"),
    ).toBeInTheDocument();
  });
});
