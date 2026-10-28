import { describe, expect, it } from "vitest";
import { isAllowedCheckoutUrl } from "@/lib/checkout-url";

const ORIGIN = "https://app.example.com";

describe("isAllowedCheckoutUrl", () => {
  it("accepts the app's own gateway checkout path", () => {
    expect(isAllowedCheckoutUrl(`${ORIGIN}/pay/checkout/order_1`, ORIGIN)).toBe(
      true,
    );
  });

  it("rejects other paths on the app's own origin", () => {
    expect(isAllowedCheckoutUrl(`${ORIGIN}/admin/users`, ORIGIN)).toBe(false);
    expect(isAllowedCheckoutUrl(`${ORIGIN}/pay/checkoutx`, ORIGIN)).toBe(false);
  });

  it("rejects foreign, protocol-relative, script and credentialed URLs", () => {
    for (const url of [
      "https://evil.example/pay/checkout/order_1",
      "//evil.example/pay/checkout/order_1",
      "/pay/checkout/order_1",
      "javascript:alert(1)",
      "data:text/html,hi",
      "https://user:pass@app.example.com/pay/checkout/o",
      " https://app.example.com/pay/checkout/o",
      42,
      null,
    ]) {
      expect(isAllowedCheckoutUrl(url, ORIGIN), String(url)).toBe(false);
    }
  });

  it("rejects the same host on a different port or scheme", () => {
    expect(
      isAllowedCheckoutUrl(
        "https://app.example.com:8443/pay/checkout/o",
        ORIGIN,
      ),
    ).toBe(false);
    expect(
      isAllowedCheckoutUrl("http://app.example.com/pay/checkout/o", ORIGIN),
    ).toBe(false);
  });

  it("accepts a configured external gateway origin", () => {
    expect(
      isAllowedCheckoutUrl("https://pay.gateway.test/session/abc", ORIGIN, [
        "https://pay.gateway.test",
      ]),
    ).toBe(true);
    expect(
      isAllowedCheckoutUrl("https://pay.gateway.test.evil/x", ORIGIN, [
        "https://pay.gateway.test",
      ]),
    ).toBe(false);
  });
});
