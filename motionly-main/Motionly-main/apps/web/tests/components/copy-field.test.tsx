import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
const clipboard = vi.hoisted(() => ({
  copyText: vi.fn(async (_text: string) => ({ ok: true as boolean })),
}));
vi.mock("@/lib/clipboard", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/clipboard")>()),
  copyText: (text: string) => clipboard.copyText(text),
}));
const apiKeyActions = vi.hoisted(() => ({
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
}));
vi.mock("@/app/(app)/account/api-keys/actions", () => apiKeyActions);

const { CopyField } = await import("@/components/copy-field");
const { ApiKeysClient } =
  await import("@/app/(app)/account/api-keys/ApiKeysClient");

const SECRET = "mtly_live_0123456789abcdef";

beforeEach(() => {
  resetNextMocks();
  clipboard.copyText.mockReset();
});

afterEach(() => {
  window.getSelection()?.removeAllRanges();
});

describe("CopyField", () => {
  it("copies, shows Copied and announces it without reading the value", async () => {
    clipboard.copyText.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    render(<CopyField value={SECRET} label="API key" testId="secret" />);

    await user.click(screen.getByRole("button", { name: "Copy" }));

    expect(clipboard.copyText).toHaveBeenCalledWith(SECRET);
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("API key copied");
    expect(status).not.toHaveTextContent(SECRET);
  });

  it("when copying fails, selects the text and says how to copy it", async () => {
    clipboard.copyText.mockResolvedValue({ ok: false });
    const user = userEvent.setup();
    render(<CopyField value={SECRET} label="API key" testId="secret" />);

    await user.click(screen.getByRole("button", { name: "Copy" }));

    expect(screen.getByText("Press Ctrl+C / ⌘C to copy.")).toBeInTheDocument();
    expect(window.getSelection()?.toString()).toBe(SECRET);
    expect(screen.getByRole("status")).toHaveTextContent(/Copy failed/);
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  });

  it("keeps the value itself out of every live region", () => {
    render(<CopyField value={SECRET} label="API key" testId="secret" />);
    const value = screen.getByTestId("secret");
    expect(value).toHaveTextContent(SECRET);
    expect(
      value.closest('[aria-live], [role="status"], [role="alert"]'),
    ).toBeNull();
  });

  it("describes the button with what it copies", () => {
    render(<CopyField value={SECRET} label="Invite link" testId="secret" />);
    expect(
      screen.getByRole("button", { name: "Copy" }),
    ).toHaveAccessibleDescription("Invite link");
  });
});

describe("ApiKeysClient one-time secret", () => {
  it("announces the new key without putting the secret in a live region", async () => {
    apiKeyActions.createApiKey.mockResolvedValue({
      ok: true,
      secret: SECRET,
      key: {
        id: "k1",
        name: "CI",
        prefix: "mtly_live",
        last4: "cdef",
        created_at: "2026-10-01T00:00:00Z",
        last_used_at: null,
        revoked_at: null,
      },
    });
    const user = userEvent.setup();
    render(<ApiKeysClient initialKeys={[]} />);
    await user.type(screen.getByLabelText(/Create API key/i), "CI");
    await user.click(screen.getByRole("button", { name: /Create key/i }));

    const secret = await screen.findByTestId("api-key-secret");
    expect(secret).toHaveTextContent(SECRET);
    expect(
      secret.closest('[aria-live], [role="status"], [role="alert"]'),
    ).toBeNull();
    const announcements = screen
      .getAllByRole("status")
      .map((node) => node.textContent ?? "");
    expect(announcements.some((text) => /API key created/.test(text))).toBe(
      true,
    );
    expect(announcements.some((text) => text.includes(SECRET))).toBe(false);
  });
});
