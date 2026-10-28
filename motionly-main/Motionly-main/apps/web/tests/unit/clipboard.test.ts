import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText, selectElementText } from "@/lib/clipboard";

const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value,
  });
}

afterEach(() => {
  if (original) Object.defineProperty(navigator, "clipboard", original);
  document.body.replaceChildren();
});

describe("copyText", () => {
  it("reports success when the clipboard accepts the text", async () => {
    const writeText = vi.fn(async () => {});
    setClipboard({ writeText });
    await expect(copyText("mtly_secret")).resolves.toEqual({ ok: true });
    expect(writeText).toHaveBeenCalledWith("mtly_secret");
  });

  it("reports failure when there is no clipboard API (insecure context)", async () => {
    setClipboard(undefined);
    await expect(copyText("mtly_secret")).resolves.toEqual({ ok: false });
  });

  it("reports failure when the browser rejects the write", async () => {
    setClipboard({
      writeText: vi.fn(async () => {
        throw new DOMException("Denied", "NotAllowedError");
      }),
    });
    await expect(copyText("mtly_secret")).resolves.toEqual({ ok: false });
  });
});

describe("selectElementText", () => {
  it("selects the element's text so the user can copy it by hand", () => {
    const code = document.createElement("code");
    code.textContent = "https://motionly.test/invite#token=abc";
    document.body.append(code);
    expect(selectElementText(code)).toBe(true);
    expect(window.getSelection()?.toString()).toBe(
      "https://motionly.test/invite#token=abc",
    );
  });
});
